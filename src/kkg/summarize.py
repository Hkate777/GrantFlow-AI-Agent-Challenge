"""摘要生成 Agent：标准化申请摘要 + 一页式项目简介 + 评审资料包（Reviewer Pack）。"""
from __future__ import annotations

from pathlib import Path

from .llm import LLM
from .models import Application, Finding
from .program import Program


def standard_summary(app: Application, extracted: dict, findings: list[Finding]) -> str:
    lines = [
        f"# 申请摘要 · {app.app_id}",
        "",
        f"- 计划：{extracted.get('_program_name', '')}",
        f"- 项目名称：{app.title}",
        f"- 申请主体：{app.applicant}",
        f"- 联系邮箱：{app.email or '（未填）'}",
        f"- 团队成员：{'、'.join(app.team) if app.team else '（未填）'}",
        f"- 自报预算：{app.fields.get('budget', '（未填）')}",
        "",
        "## 关键信息（AI 抽取）",
    ]
    for k, v in extracted.items():
        if k.startswith("_") or v in (None, ""):
            continue
        lines.append(f"- {k}：{v}")
    lines += ["", "## 审查结论"]
    if findings:
        for f in findings:
            flag = "🛈" if f.severity.value == "info" else ("⚠️" if f.severity.value == "warning" else "❌")
            human = "（需人工复核）" if f.needs_human else ""
            lines.append(f"- {flag} [{f.finding_type.value}] {f.message}{human}")
    else:
        lines.append("- ✅ 未发现缺失、矛盾或异常。")
    return "\n".join(lines)


def one_pager(app: Application, extracted: dict, llm: LLM) -> str:
    """一页式项目简介：LLM 凝练（MockLLM 为启发式摘要）。"""
    corpus = "\n".join(d.text for d in app.docs if d.text)[:6000]
    brief = llm.complete(
        "Summarize this grant application in at most 300 words for reviewers. "
        "Cover: problem, solution, target users, budget, expected outcomes.",
        corpus or app.title,
    )
    return "\n".join([
        f"# 一页式项目简介 · {app.app_id}",
        "",
        f"**{app.title}** — {app.applicant}",
        "",
        brief,
        "",
        f"> 评审维度：{extracted.get('_criteria_hint', '')}",
    ])


def reviewer_pack(app: Application, prog: Program, extracted: dict,
                  findings: list[Finding], out_dir: str | Path) -> Path:
    """生成单个申请的评审资料包目录：summary.md + one_pager.md + checklist.md。"""
    pack = Path(out_dir) / app.app_id
    pack.mkdir(parents=True, exist_ok=True)

    (pack / "summary.md").write_text(standard_summary(app, extracted, findings), encoding="utf-8")
    (pack / "one_pager.md").write_text(one_pager(app, extracted, _NoopLLM()), encoding="utf-8")

    checklist = ["# 评审核对清单", "", f"计划：{prog.name}（截止 {prog.deadline or 'N/A'}）", "", "## 评审维度与权重"]
    for c in prog.criteria:
        checklist.append(f"- [ ] {c.name}（权重 {c.weight}，满分 {c.max_score}）：____")
    checklist += ["", "## 资格审查",
                  "- [ ] 必填资料齐全",
                  "- [ ] 附件可正常打开",
                  "- [ ] 符合资格条件",
                  "- [ ] 无利益冲突",
                  "", "## 评审意见", ""]
    (pack / "checklist.md").write_text("\n".join(checklist), encoding="utf-8")
    return pack


class _NoopLLM:
    """占位：一页式简介由外层传入真实 LLM 时可在 pipeline 中覆盖；这里提供兜底。"""

    def complete(self, system: str, user: str) -> str:
        lines = [ln.strip() for ln in user.splitlines() if ln.strip()]
        return " ".join(lines[:5])[:500] or "（无正文可摘要）"
