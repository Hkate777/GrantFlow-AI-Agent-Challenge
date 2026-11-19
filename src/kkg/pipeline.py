"""Agentic Workflow 编排：把各子 Agent 串成端到端流程，全程记录审计日志。

流程（对应场景 16 功能要求）：
  ingest   → 收集多渠道提交资料与附件，解析多格式文档
  extract  → 结构化抽取关键信息（LLM，Mock 可离线复现）
  check    → 完整性 / 格式 / 资格 / 利益冲突 / 预算审查
  verify   → 跨源交叉验证 + 跨申请查重 + 预算异常检测
  summarize→ 标准化摘要、一页式简介、评审资料包
  review   → 汇总评分评语、识别分歧、生成结果通知与待办
  track    → 里程碑 / 资助发放 / 定期报告追踪 + 预警 + 管理报告
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
from .review import aggregate, followup_todos, result_notice
from .summarize import reviewer_pack
from .track import management_report


class Pipeline:
    def __init__(self, program: Program, llm: LLM | None = None,
                 pass_threshold: float = 60.0) -> None:
        self.prog = program
        self.llm = llm or get_llm("auto")
        self.pass_threshold = pass_threshold
        self.audit = AuditTrail()

    # ---------- 阶段 1：受理与审查 ----------
    def intake(self, app: Application) -> tuple[dict, list[Finding]]:
        self.audit.log("intake", "start", f"{app.app_id} {app.title}")
        extracted = extract_fields(app, self.llm)
        extracted["_program_name"] = self.prog.name
        extracted["_criteria_hint"] = " / ".join(c.name for c in self.prog.criteria)
        self.audit.log("extract", "fields", f"keys={list(extracted)}")

        findings = check_completeness(app, self.prog)
        findings += cross_verify(app, extracted)
        self.audit.log("check", "done", f"findings={len(findings)}")
        return extracted, findings

    # ---------- 阶段 2：批次级检查 ----------
    def batch_checks(self, apps: list[Application]) -> list[Finding]:
        findings = detect_duplicates(apps) + anomaly_scan(apps)
        self.audit.log("batch", "done", f"findings={len(findings)}")
        return findings

    # ---------- 阶段 3：生成评审资料包 ----------
    def build_packs(self, apps: list[Application], extracts: dict[str, dict],
                    all_findings: dict[str, list[Finding]], out_dir: str | Path) -> dict[str, str]:
        paths = {}
        for a in apps:
            pack = reviewer_pack(a, self.prog, extracts[a.app_id],
                                 all_findings.get(a.app_id, []), out_dir)
            paths[a.app_id] = str(pack)
        self.audit.log("summarize", "packs", f"n={len(paths)}")
        return paths

    # ---------- 阶段 4：评审汇总与通知 ----------
    def review(self, app: Application, scores: list[ReviewScore]) -> dict:
        agg = aggregate(scores, self.prog)
        agg["notice"] = result_notice(
            app.app_id, app.title, agg,
            passed=(agg.get("weighted_total") or 0) >= self.pass_threshold)
        agg["todos"] = followup_todos(app.app_id, agg)
        self.audit.log("review", "aggregated",
                       f"{app.app_id} total={agg.get('weighted_total')}")
        return agg

    # ---------- 阶段 5：项目追踪 ----------
    def track(self, app: Application, milestones: list[Milestone],
              periodic_reports: list[str] | None = None) -> str:
        report = management_report(app.app_id, app.title, milestones, periodic_reports)
        self.audit.log("track", "report", app.app_id)
        return report

    def audit_json(self) -> str:
        return self.audit.to_json()


def load_applications(root: str | Path) -> list[Application]:
    """从目录加载申请：每个子目录 = 一份申请，含 meta.json + 任意附件。"""
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
