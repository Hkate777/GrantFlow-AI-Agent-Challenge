"""Agentic Workflow 編排：把各子 Agent 串成端到端流程，全程記錄審計日誌。

流程（對應場景 16 功能要求）：
  ingest   → 收集多渠道提交資料與附件，解析多格式文檔
  extract  → 結構化抽取關鍵信息（LLM，Mock 可離線復現）
  check    → 完整性 / 格式 / 資格 / 利益衝突 / 預算審查
  verify   → 跨源交叉驗證 + 跨申請查重 + 預算異常檢測
  summarize→ 標準化摘要、一頁式簡介、評審資料包
  review   → 彙總評分評語、識別分歧、生成結果通知與待辦
  track    → 里程碑 / 資助發放 / 定期報告追蹤 + 預警 + 管理報告
"""
from __future__ import annotations

import json
from pathlib import Path

from .check import check_completeness, detect_duplicates
from .extract import anomaly_scan, cross_verify, extract_fields
from .ingest import ingest_dir
from .llm import LLM, get_llm
from .models import Application, AuditTrail, Finding, Milestone, ReviewScore
from .program import Program
from .review import assess, followup_todos
from .summarize import reviewer_pack
from .track import management_report


class Pipeline:
    def __init__(self, program: Program, llm: LLM | None = None,
                 pass_threshold: float | None = None) -> None:
        self.prog = program
        self.llm = llm or get_llm("auto")
        self.pass_threshold = program.pass_threshold if pass_threshold is None else pass_threshold
        if not 0 <= self.pass_threshold <= 100:
            raise ValueError("通過分數門檻必須介於 0 與 100 之間。")
        self.audit = AuditTrail()

    # ---------- 階段 1：受理與審查 ----------
    def intake(self, app: Application) -> tuple[dict, list[Finding]]:
        self.audit.log("intake", "start", f"{app.app_id} · {self.prog.program_id}@{self.prog.rule_version}")
        extracted = extract_fields(app, self.llm)
        extracted["_program_name"] = self.prog.name
        extracted["_criteria_hint"] = " / ".join(c.name for c in self.prog.criteria)
        self.audit.log("extract", "fields", f"keys={list(extracted)}")

        findings = check_completeness(app, self.prog)
        findings += cross_verify(app, extracted)
        self.audit.log("check", "done", f"{app.app_id} · findings={len(findings)}")
        return extracted, findings

    # ---------- 階段 2：批次級檢查 ----------
    def batch_checks(self, apps: list[Application]) -> list[Finding]:
        findings = detect_duplicates(apps) + anomaly_scan(apps)
        self.audit.log("batch", "done", f"findings={len(findings)}")
        return findings

    # ---------- 階段 3：生成評審資料包 ----------
    def build_packs(self, apps: list[Application], extracts: dict[str, dict],
                    all_findings: dict[str, list[Finding]], out_dir: str | Path) -> dict[str, str]:
        paths = {}
        for a in apps:
            pack = reviewer_pack(a, self.prog, extracts[a.app_id],
                                 all_findings.get(a.app_id, []), out_dir)
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
        report = management_report(app.app_id, app.title, milestones, periodic_reports)
        self.audit.log("track", "report", app.app_id)
        return report

    def audit_json(self) -> str:
        return self.audit.to_json()


def load_applications(root: str | Path, *, errors: list[str] | None = None) -> list[Application]:
    """從目錄載入申請：每個子目錄 = 一份申請，含 meta.json + 任意附件。

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
            app = load_application(sub)
        except (ValueError, OSError, UnicodeError) as exc:
            errs.append(f"{sub.name}: {exc}")
            continue
        apps.append(app)
    return apps
