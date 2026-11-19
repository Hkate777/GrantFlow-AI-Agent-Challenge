"""Agentic Workflow編排：把各子Agent串成端到端流程，全程記錄審計日誌。

流程（對應場景16功能要求）：
  ingest至 收集多渠道提交資料與附件，解析多格式文檔
  extract至 結構化抽取關鍵信息（LLM，Mock可離線復現）
  check至 完整性 / 格式 / 資格 / 利益衝突 / 預算審查
  verify至 跨源交叉驗證 + 跨申請查重 + 預算異常檢測
  summarize至 標準化摘要、一頁式簡介、評審資料包
  review至 彙總評分評語、識別分歧、生成結果通知與待辦
  track至 里程碑 / 資助發放 / 定期報告追蹤 + 預警 + 管理報告
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
from .region import Region
from .review import aggregate, followup_todos, result_notice
from .summarize import reviewer_pack
from .track import management_report


class Pipeline:
    def __init__(self, program: Program, llm: LLM | None = None,
                 pass_threshold: float = 60.0, region: Region | None = None) -> None:
        self.prog = program
        self.llm = llm or get_llm("auto")
        self.pass_threshold = pass_threshold
        self.region = region
        self.audit = AuditTrail()

    # ---------- 階段 1：受理與審查 ----------
    def intake(self, app: Application) -> tuple[dict, list[Finding]]:
        self.audit.log("intake", "start", f"{app.app_id} {app.title}")
        extracted = extract_fields(app, self.llm)
        extracted["_program_name"] = self.prog.name
        extracted["_criteria_hint"] = " / ".join(c.name for c in self.prog.criteria)
        self.audit.log("extract", "fields", f"keys={list(extracted)}")

        findings = check_completeness(app, self.prog)
        findings += cross_verify(app, extracted)
        if self.region:
            from .check import check_region
            findings += check_region(app, self.region)
            extracted["_region"] = self.region.name
            extracted["_region_conventions"] = self.region.review_conventions
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
        paths = {}
        for a in apps:
            pack = reviewer_pack(a, self.prog, extracts[a.app_id],
                                 all_findings.get(a.app_id, []), out_dir)
            paths[a.app_id] = str(pack)
        self.audit.log("summarize", "packs", f"n={len(paths)}")
        return paths

    # ---------- 階段 4：評審彙總與通知 ----------
    def review(self, app: Application, scores: list[ReviewScore]) -> dict:
        agg = aggregate(scores, self.prog)
        agg["notice"] = result_notice(
            app.app_id, app.title, agg,
            passed=(agg.get("weighted_total") or 0) >= self.pass_threshold)
        agg["todos"] = followup_todos(app.app_id, agg)
        self.audit.log("review", "aggregated",
                       f"{app.app_id} total={agg.get('weighted_total')}")
        return agg

    # ---------- 階段 5：項目追蹤 ----------
    def track(self, app: Application, milestones: list[Milestone],
              periodic_reports: list[str] | None = None) -> str:
        report = management_report(app.app_id, app.title, milestones, periodic_reports)
        self.audit.log("track", "report", app.app_id)
        return report

    def audit_json(self) -> str:
        return self.audit.to_json()


def load_applications(root: str | Path) -> list[Application]:
    """從目錄加載申請：每個子目錄 = 一份申請，含meta.json + 任意附件。"""
    apps: list[Application] = []
    for sub in sorted(Path(root).iterdir()):
        if not sub.is_dir():
            continue
        meta_file = sub / "meta.json"
        meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
        app = Application(
            app_id=meta.get("app_id", sub.name),
            program=meta.get("program", ""),
            title=meta.get("title", sub.name),
            applicant=meta.get("applicant", ""),
            email=meta.get("email", ""),
            team=meta.get("team", []),
            fields=meta.get("fields", {}),
            docs=ingest_dir(sub),
        )
        app.docs = [d for d in app.docs if d.name != "meta.json"]
        apps.append(app)
    return apps
