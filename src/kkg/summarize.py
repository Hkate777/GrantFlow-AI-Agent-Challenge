"""摘要生成Agent：標準化申請摘要 + 一頁式項目簡介 + 評審資料包（Reviewer Pack）。"""
from __future__ import annotations

from pathlib import Path

from .llm import LLM
from .models import Application, Finding
from .program import Program


def standard_summary(app: Application, extracted: dict, findings: list[Finding]) -> str:
    lines = [
        f"# 申請摘要 · {app.app_id}",
        "",
        f"- 計劃：{extracted.get('_program_name', '')}",
        f"- 項目名稱：{app.title}",
        f"- 申請主體：{app.applicant}",
        f"- 聯繫郵箱：{app.email or '（未填）'}",
        f"- 團隊成員：{'、'.join(app.team) if app.team else '（未填）'}",
        f"- 自報預算：{app.fields.get('budget', '（未填）')}",
        "",
        "## 關鍵信息（AI抽取）",
    ]
    for k, v in extracted.items():
        if k.startswith("_") or v in (None, ""):
            continue
        lines.append(f"- {k}：{v}")
    lines += ["", "## 審查結論"]
    if findings:
        for f in findings:
            flag = "[提示]" if f.severity.value == "info" else ("[注意]" if f.severity.value == "warning" else "[未通過]")
            human = "（需人工複核）" if f.needs_human else ""
            lines.append(f"- {flag} [{f.finding_type.value}] {f.message}{human}")
    else:
        lines.append("- [通過] 未發現缺失、矛盾或異常。")
    return "\n".join(lines)


def one_pager(app: Application, extracted: dict, llm: LLM) -> str:
    """一頁式項目簡介：LLM凝練（MockLLM為啓發式摘要）。"""
    corpus = "\n".join(d.text for d in app.docs if d.text)[:6000]
    brief = llm.complete(
        "Summarize this grant application in at most 300 words for reviewers. "
        "Cover: problem, solution, target users, budget, expected outcomes.",
        corpus or app.title,
    )
    return "\n".join([
        f"# 一頁式項目簡介 · {app.app_id}",
        "",
        f"**{app.title}** — {app.applicant}",
        "",
        brief,
        "",
        f"> 評審維度：{extracted.get('_criteria_hint', '')}",
    ])


def reviewer_pack(app: Application, prog: Program, extracted: dict,
                  findings: list[Finding], out_dir: str | Path,
                  llm: LLM | None = None) -> Path:
    """生成單個申請的評審資料包目錄：summary.md + one_pager.md + checklist.md。

    app應為去識別化視圖（由Pipeline傳入），確保對外文件不含原始敏感資訊。
    llm可選：傳入時一頁式簡介使用真實模型；缺省時用本地啓發式兜底。
    """
    pack = Path(out_dir) / app.app_id
    pack.mkdir(parents=True, exist_ok=True)

    (pack / "summary.md").write_text(standard_summary(app, extracted, findings), encoding="utf-8")
    (pack / "one_pager.md").write_text(one_pager(app, extracted, llm or _NoopLLM()), encoding="utf-8")

    checklist = ["# 評審核對清單", "", f"計劃：{prog.name}（截止{prog.deadline or 'N/A'}）", "", "## 評審維度與權重"]
    for c in prog.criteria:
        checklist.append(f"- [ ] {c.name}（權重{c.weight}，滿分{c.max_score}）：____")
    checklist += ["", "## 資格審查",
                  "- [ ] 必填資料齊全",
                  "- [ ] 附件可正常打開",
                  "- [ ] 符合資格條件",
                  "- [ ] 無利益衝突",
                  "", "## 評審意見", ""]
    (pack / "checklist.md").write_text("\n".join(checklist), encoding="utf-8")
    return pack


class _NoopLLM:
    """佔位：一頁式簡介由外層傳入真實LLM時可在pipeline中覆蓋；這裏提供兜底。"""

    def complete(self, system: str, user: str) -> str:
        lines = [ln.strip() for ln in user.splitlines() if ln.strip()]
        return " ".join(lines[:5])[:500] or "（無正文可摘要）"
