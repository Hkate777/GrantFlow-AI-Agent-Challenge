"""按計劃設定檢查欄位、附件與資格。"""
from __future__ import annotations

import math
import re
import unicodedata
from datetime import date
from typing import Any

from .models import Application, Finding, FindingType, Severity
from .money import parse_hkd
from .program import Program

_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}$")


def _text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _to_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "").replace("HK$", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _iso_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _field_findings(app: Application, prog: Program) -> list[Finding]:
    findings = []
    for rule in prog.required_fields:
        value = app.fields.get(rule.key)
        label = rule.label or rule.key
        if value is None or value == [] or (isinstance(value, str) and not value.strip()):
            findings.append(Finding(FindingType.MISSING, Severity.ERROR, f"必填欄位缺失：{label}",
                                    source=f"fields.{rule.key}", needs_human=True))
            continue
        if rule.ftype == "number":
            number = parse_hkd(value) if rule.key == "budget" else _to_number(value)
            valid = (number is not None and (rule.min_value is None or number >= rule.min_value)
                     and (rule.max_value is None or number <= rule.max_value))
        elif rule.ftype == "email":
            valid = isinstance(value, str) and _EMAIL_RE.fullmatch(value.strip()) is not None
        elif rule.ftype == "list":
            valid = isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)
        elif rule.ftype == "date":
            valid = _iso_date(value)
        else:
            valid = isinstance(value, str)
        if not valid:
            findings.append(Finding(FindingType.FORMAT, Severity.ERROR, f"「{label}」格式或範圍不符。",
                                    source=f"fields.{rule.key}", needs_human=True))
    return findings


def _document_findings(app: Application, prog: Program) -> list[Finding]:
    findings = []
    for rule in prog.required_docs:
        severity = Severity.ERROR if rule.required else Severity.INFO
        matching = [doc for doc in app.docs if _text(rule.name) in _text(doc.name)]
        eligible = [doc for doc in matching if rule.doc_type == "any" or doc.doc_type.value == rule.doc_type]
        if not matching:
            findings.append(Finding(FindingType.MISSING, severity,
                                    f"{'必備' if rule.required else '建議'}附件缺失：{rule.name}",
                                    source="required_docs", needs_human=rule.required))
        elif not eligible:
            findings.append(Finding(FindingType.FORMAT, severity,
                                    f"附件「{rule.name}」須使用 {rule.doc_type.upper()} 格式。",
                                    source="required_docs", needs_human=rule.required))
        elif rule.required and all(doc.error and not doc.text.strip() for doc in eligible):
            findings.append(Finding(FindingType.FORMAT, Severity.ERROR,
                                    f"必備附件「{rule.name}」沒有可讀內容。",
                                    source="required_docs", needs_human=True))
    for doc in app.docs:
        if doc.error:
            findings.append(Finding(FindingType.FORMAT, Severity.WARNING,
                                    f"附件「{doc.name}」需核對讀取結果。", detail=doc.error,
                                    source=doc.name, needs_human=True))
    return findings


def _contains(text: str, keyword: str) -> bool:
    key = _text(keyword)
    if key.isascii():
        return re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", text) is not None
    return key in text


def _eligibility_findings(app: Application, prog: Program) -> list[Finding]:
    findings = []
    rule = prog.eligibility
    if app.program and app.program != prog.program_id:
        findings.append(Finding(FindingType.ELIGIBILITY, Severity.ERROR, "申請所屬計劃與所選規則不符。",
                                source="program_id", needs_human=True))
    org = _text(str(app.fields.get("org_type") or ""))
    if rule.allowed_org_types and org not in {_text(value) for value in rule.allowed_org_types}:
        findings.append(Finding(FindingType.ELIGIBILITY, Severity.ERROR, "申請主體類型不在計劃準許範圍。",
                                source="allowed_org_types", needs_human=True))
    if not rule.min_team <= len(app.team) <= rule.max_team:
        findings.append(Finding(FindingType.ELIGIBILITY, Severity.ERROR,
                                f"團隊人數 {len(app.team)} 不在允許範圍 {rule.min_team}–{rule.max_team}。",
                                source="eligibility", needs_human=True))
    submitted = app.fields.get("submitted_at")
    if prog.deadline and _iso_date(submitted) and submitted > prog.deadline:
        findings.append(Finding(FindingType.ELIGIBILITY, Severity.ERROR, "申請提交日期已超過計劃截止日期。",
                                source="deadline", needs_human=True))
    summary = _text(" ".join([app.title, *(str(value) for value in app.fields.values())]))
    for keyword in rule.excluded_keywords:
        if _contains(summary, keyword):
            findings.append(Finding(FindingType.ELIGIBILITY, Severity.CRITICAL,
                                    f"申請內容觸發資格排除條款「{keyword}」。",
                                    source="eligibility", needs_human=True))
    budget = parse_hkd(app.fields.get("budget"))
    if prog.budget_cap is not None and budget is not None and budget > prog.budget_cap:
        findings.append(Finding(FindingType.ELIGIBILITY, Severity.ERROR,
                                f"預算 {budget:,.0f} 超出計劃上限 {prog.budget_cap:,.0f}。",
                                source="budget_cap", needs_human=True))
    return findings


def _conflict_findings(app: Application, prog: Program) -> list[Finding]:
    sources = [(f"fields.{key}", str(value)) for key, value in app.fields.items()]
    sources.extend([("application.title", app.title), ("application.applicant", app.applicant),
                    ("application.team", " ".join(app.team))])
    for doc in app.docs:
        if doc.segments:
            sources.extend((f"{doc.name} · {segment.locator}", segment.text) for segment in doc.segments)
        else:
            sources.append((doc.name, doc.text))
    normalized = [(source, _text(value)) for source, value in sources]
    findings = []
    for reviewer, keywords in prog.conflict_rules.items():
        evidence = [(source, word) for source, text in normalized for word in keywords if _contains(text, word)]
        if evidence:
            source, keyword = evidence[0]
            findings.append(Finding(FindingType.CONFLICT, Severity.CRITICAL,
                                    f"潛在利益衝突：評審員「{reviewer}」與「{keyword}」有關，請安排迴避核查。",
                                    detail=reviewer, source=source, needs_human=True))
    return findings


def check_completeness(app: Application, prog: Program) -> list[Finding]:
    return (_field_findings(app, prog) + _document_findings(app, prog)
            + _eligibility_findings(app, prog) + _conflict_findings(app, prog))


def detect_duplicates(apps: list[Application]) -> list[Finding]:
    seen: dict[tuple[str, str], set[str]] = {}
    for app in apps:
        for label, value in (("電郵", app.email), ("項目名稱", app.title), ("申請人", app.applicant)):
            if value.strip():
                seen.setdefault((label, _text(value)), set()).add(app.app_id)
    groups: dict[tuple[str, ...], set[str]] = {}
    for (label, _), app_ids in seen.items():
        if len(app_ids) > 1:
            groups.setdefault(tuple(sorted(app_ids)), set()).add(label)
    return [
        Finding(FindingType.DUPLICATE, Severity.WARNING,
                f"疑似重複申請：{'、'.join(ids)} 有相同的{'、'.join(sorted(labels))}。",
                source="dedup", needs_human=True, application_ids=list(ids))
        for ids, labels in sorted(groups.items())
    ]
