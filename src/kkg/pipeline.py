"""Agentic Workflow編排：把各子Agent串成端到端流程，全程記錄審計日誌。

流程（對應場景16功能要求）：
  ingest：收集多渠道提交資料與附件，解析多格式文檔
  extract：結構化抽取關鍵信息（LLM，Mock可離線復現）
  check：完整性 / 格式 / 資格 / 利益衝突 / 預算審查
  verify：跨源交叉驗證 + 跨申請查重 + 預算異常檢測
  summarize：標準化摘要、一頁式簡介、評審資料包
  review：彙總評分評語、識別分歧、生成結果通知與待辦
  track：里程碑 / 資助發放 / 定期報告追蹤 + 預警 + 管理報告
"""
from __future__ import annotations

import json
from pathlib import Path

from .check import check_completeness, detect_duplicates
from .extract import anomaly_scan, cross_verify, extract_fields
from .guard import injection_findings, scan_prompt_injection
from .ingest import ingest_dir
from .llm import LLM, get_llm
from .models import Application, AuditTrail, Finding, Milestone, ReviewScore
from .privacy import Redactor
from .program import Program
from .region import Region
from .review import assess, followup_todos
from .summarize import reviewer_pack
from .track import management_report


class Pipeline:
    def __init__(self, program: Program, llm: LLM | None = None,
                 pass_threshold: float = 60.0, region: Region | None = None,
                 privacy: bool = True) -> None:
        self.prog = program
        self.llm = llm or get_llm("auto")
        self.pass_threshold = pass_threshold
        self.region = region
        self.privacy = privacy
        self.redactor = Redactor() if privacy else None
        self.audit = AuditTrail()

    # ---------- 階段 1：受理與審查 ----------
    def intake(self, app: Application) -> tuple[dict, list[Finding]]:
        self.audit.log("intake", "start", f"{app.app_id} {app.title}")

        # 注入偵測前移：必須在任何模型調用之前完成，規則式訊號只作提示、
        # 絶不執行其內容；即使使用 MockLLM（不發出模型請求）也照樣掃描。
        blob = "\n".join([app.title, app.applicant,
                          *(str(v) for v in app.fields.values()),
                          *(d.text for d in app.docs if d.text)])
        security_signals: list = []
        if blob.strip():
            security_signals = scan_prompt_injection(blob, locator=app.app_id)
            self.audit.log("guard", "pre_model_scan",
                           f"{app.app_id} · {len(security_signals)}處 · 規則式提示，不保證攔截")
        findings = injection_findings(security_signals, app_id=app.app_id)

        # 私隱防護：送LLM前先去識別化，抽取與交叉驗證全程只見佔位符；
        # 不再把抽取結果 restore() 回明文，避免敏感值沿 Finding 流入評審包。
        source = self.redactor.safe_view(app) if self.redactor else app
        extracted = extract_fields(source, self.llm)
        if self.redactor:
            self.audit.log("privacy", "redacted", self.redactor.report.summary())
        extracted["_program_name"] = self.prog.name
        extracted["_criteria_hint"] = " / ".join(c.name for c in self.prog.criteria)
        self.audit.log("extract", "fields", f"keys={list(extracted)}")

        findings += check_completeness(app, self.prog)
        findings += cross_verify(source, extracted)
        if security_signals:
            self.audit.log("guard", "injection_detected",
                           f"{app.app_id} · {len(security_signals)}處 · "
                           f"{[s.category for s in security_signals]}")
        if self.region:
            from .check import check_region, normalize_for_region
            from . import script as script_std
            findings += check_region(app, self.region)
            extracted["_region"] = self.region.name
            extracted["_region_conventions"] = self.region.review_conventions
            # 字形標準化：為下游輸出提供符合地區標準的文本，並記錄逐處審計。
            if self.region.code in script_std.supported_regions():
                corpus = "\n".join(d.text for d in app.docs if d.text)
                if corpus:
                    audit = script_std.audit_text(corpus, self.region.code)
                    extracted["_script_target"] = audit.target_label
                    extracted["_script_normalized"] = normalize_for_region(corpus, self.region)
                    self.audit.log("script", audit.summary(), self.region.code)
            self.audit.log("region", "applied", self.region.code)
        self.audit.log("check", "done", f"findings={len(findings)}")
        return extracted, findings

    # ---------- 階段 2：批次級檢查 ----------
    def batch_checks(self, apps: list[Application]) -> list[Finding]:
        findings = detect_duplicates(apps) + anomaly_scan(apps)
        self.audit.log("batch", "done", f"findings={len(findings)}")
        return findings

    # ---------- 階段 3：生成評審資料包 ----------
    def build_packs(self, apps: list[Application], extracts: dict[str, dict],
                    all_findings: dict[str, list[Finding]], out_dir: str | Path) -> dict[str, str]:
        from .output_gate import gate_bundle, gate_directory
        from .summarize import reviewer_pack

        paths = {}
        for a in apps:
            # 對外評審資料包一律經輸出閘門：去識別化視圖 + 逐檔 ensure_safe_output。
            bundle = gate_bundle(a, extracts[a.app_id], all_findings.get(a.app_id, []),
                                 redactor=self.redactor)
            pack = reviewer_pack(bundle["application"], self.prog, bundle["extracted"],
                                 bundle["findings"], out_dir, llm=self.llm)
            hits = gate_directory(pack, bundle["redactor"])
            if hits:
                self.audit.log("output_gate", "redacted", f"{a.app_id} · {hits}")
            paths[a.app_id] = str(pack)
        self.audit.log("summarize", "packs", f"n={len(paths)}")
        return paths

    # ---------- 階段 4：評審彙總與通知 ----------
    def review(self, app: Application, scores: list[ReviewScore], *,
               findings: list[Finding] | None = None, reviewed: bool = False) -> dict:
        if findings is None:
            _, findings = self.intake(app)
        agg = assess(scores, self.prog, findings, pass_threshold=self.pass_threshold, reviewed=reviewed)
        agg["todos"] = followup_todos(app.app_id, agg)
        self.audit.log("review", "aggregated",
                       f"{app.app_id} · {agg['status']} · total={agg['weighted_total']}")
        return agg

    # ---------- 階段 5：項目追蹤 ----------
    def track(self, app: Application, milestones: list[Milestone],
              periodic_reports: list[str] | None = None) -> str:
        # 以地區當地今天判斷到期/確認日期，避免沿用伺服器本地時間出錯。
        tz = self.region.timezone if self.region else ""
        report = management_report(app.app_id, app.title, milestones, periodic_reports, tz=tz)
        self.audit.log("track", "report", f"{app.app_id} · tz={tz or 'local'}")
        return report

    def audit_json(self) -> str:
        return self.audit.to_json()


def load_applications(root: str | Path, *, errors: list[str] | None = None) -> list[Application]:
    """從目錄加載申請：每個子目錄 = 一份申請，含meta.json + 任意附件。

    與 Web 收件共用 ``submission.load_application`` 的表單/申請編號校驗；
    壞掉的 ``meta.json`` 只記入 ``errors`` 並跳過，不中斷其餘申請。
    """
    from .submission import load_application  # 延遲導入，避免與 submission 循環依賴

    apps: list[Application] = []
    errs = errors if errors is not None else []
    for sub in sorted(Path(root).iterdir()):
        if not sub.is_dir():
            continue
        try:
            apps.append(load_application(sub))
        except (ValueError, OSError, UnicodeError) as exc:
            errs.append(f"{sub.name}: {exc}")
            continue
    return apps
