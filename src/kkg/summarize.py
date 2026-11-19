"""建立申請摘要、一頁式項目簡介和評審核對清單。"""
from __future__ import annotations

from html import escape
from pathlib import Path

from .evidence import FIELD_LABELS, Evidence
from .extract import extract_fields
from .llm import LLM
from .models import Application, Finding, Severity
from .money import format_hkd, parse_hkd
from .program import Program

_BRIEF_FIELDS = ("objectives", "target_users", "proposal", "requested_budget", "expected_outcomes")
_SEVERITIES = {Severity.INFO: "提示", Severity.WARNING: "警告", Severity.ERROR: "錯誤", Severity.CRITICAL: "嚴重"}


def _inline(value: object, limit: int | None = None) -> str:
    text = " ".join(str(value).split())
    if limit is not None and len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return escape(text, quote=False)


def _display(key: str, value: object, limit: int | None = None) -> str:
    if key == "requested_budget":
        amount = parse_hkd(value)
        if amount is not None:
            return format_hkd(amount)
    if isinstance(value, list):
        value = "、".join(str(item) for item in value)
    return _inline(value, limit)


def _location(row: Evidence) -> str:
    return f"{_inline(row['source'])} · {_inline(row['locator'])}"


def standard_summary(app: Application, extracted: dict, findings: list[Finding]) -> str:
    lines = [
        f"# 申請摘要 · {_inline(app.app_id)}", "",
        f"- 計劃：{_inline(extracted.get('_program_name') or app.program or '（未填）')}",
        f"- 項目名稱：{_inline(app.title)}",
        f"- 申請主體：{_inline(app.applicant)}",
        f"- 聯絡電郵：{_inline(app.email or '（未填）')}",
        f"- 團隊成員：{_display('team_members', app.team or '（未填）')}",
        f"- 申報預算：{_display('requested_budget', app.fields.get('budget', '（未填）'))}",
    ]
    fields = [(key, value) for key, value in extracted.items() if not key.startswith("_") and value not in (None, "")]
    if fields:
        lines += ["", "## 擷取欄位", ""]
        for key, value in fields:
            lines.append(f"- {FIELD_LABELS.get(key, key)}：{_display(key, value, 500)}")

    evidence: dict[str, list[Evidence]] = extracted.get("_evidence", {})
    if evidence:
        lines += ["", "## 原文佐證", ""]
        for key, label in FIELD_LABELS.items():
            for row in evidence.get(key, []):
                lines.append(f"- **{label}** · {_location(row)} · {_display(key, row['value'], 230)}")
                lines.append(f"  - 原文：「{_inline(row['quote'], 360)}」")

    lines += ["", "## 審查結果", ""]
    if findings:
        for finding in findings:
            human = "（需人工覆核）" if finding.needs_human else ""
            lines.append(f"- **{_SEVERITIES[finding.severity]}** · {_inline(finding.message)}{human}")
            lines.extend(f"  - {_inline(detail, 420)}" for detail in finding.detail.splitlines() if detail.strip())
    else:
        lines.append("- 未發現資料缺漏、矛盾或異常。")
    return "\n".join(lines) + "\n"


def _render_one_pager(app: Application, extracted: dict) -> str:
    lines = [f"# 一頁式項目簡介 · {_inline(app.app_id)}", "", f"**{_inline(app.title)}** · {_inline(app.applicant)}"]
    evidence: dict[str, list[Evidence]] = extracted.get("_evidence", {})
    for key in _BRIEF_FIELDS:
        label = FIELD_LABELS[key]
        rows = evidence.get(key, [])
        value = extracted.get(key)
        if key == "requested_budget":
            form = app.fields.get("budget")
            budget = []
            if form not in (None, ""):
                budget.append(f"- 表單 · {_display(key, form)}")
            sources: dict[tuple[str, str | float], Evidence] = {}
            for row in rows:
                sources.setdefault((row["source"], row["value"]), row)
            for row in list(sources.values())[:4]:
                budget.append(f"- {_location(row)} · {_display(key, row['value'])}")
            if len(sources) > 4:
                budget.append(f"- 其餘 {len(sources) - 4} 項預算佐證列於申請摘要。")
            if not rows and value not in (None, ""):
                budget.append(f"- 附件 · {_display(key, value)}")
            if budget:
                lines += ["", f"## {label}", "", *budget]
            continue

        if value in (None, ""):
            value = app.fields.get(key)
        if value not in (None, "", []):
            lines += ["", f"## {label}", "", _display(key, value, 340)]
            source = next((row for row in rows if row["value"] == value), None)
            if source:
                lines += ["", f"*{_location(source)}*"]

    if extracted.get("_criteria_hint"):
        lines += ["", f"> 評審準則 · {_inline(extracted['_criteria_hint'])}"]
    return "\n".join(lines) + "\n"


def one_pager(app: Application, extracted: dict, llm: LLM) -> str:
    if "_evidence" not in extracted:
        extracted = {**extract_fields(app, llm), **extracted}
    return _render_one_pager(app, extracted)


def reviewer_pack(app: Application, prog: Program, extracted: dict,
                  findings: list[Finding], out_dir: str | Path) -> Path:
    """輸出 `summary.md`、`one_pager.md` 與 `checklist.md`。"""
    pack = Path(out_dir) / app.app_id
    pack.mkdir(parents=True, exist_ok=True)
    fields = {"_program_name": prog.name, "_criteria_hint": " / ".join(c.name for c in prog.criteria), **extracted}
    (pack / "summary.md").write_text(standard_summary(app, fields, findings), encoding="utf-8")
    (pack / "one_pager.md").write_text(_render_one_pager(app, fields), encoding="utf-8")

    checklist = ["# 評審核對清單", "", f"計劃：{_inline(prog.name)}（截止 {_inline(prog.deadline or '未設定')}）",
                 "", "## 評審準則及權重", ""]
    for criterion in prog.criteria:
        checklist.append(f"- [ ] {_inline(criterion.name)}（權重 {criterion.weight}，滿分 {criterion.max_score}）：____")
    checklist += ["", "## 資格審查", "",
                  "- [ ] 必填資料齊全", "- [ ] 附件可正常開啟", "- [ ] 符合資格條件", "- [ ] 已核對利益衝突"]
    human = [finding for finding in findings if finding.needs_human]
    if human:
        checklist += ["", "## 待人工覆核", ""]
        checklist.extend(f"- [ ] {_inline(finding.message)}" for finding in human)
    checklist += ["", "## 評審意見", ""]
    (pack / "checklist.md").write_text("\n".join(checklist), encoding="utf-8")
    return pack
