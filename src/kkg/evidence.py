"""從原文標籤、段落和表格擷取有位置的欄位佐證。"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from typing import TypedDict

from .models import DocType, Document, EvidenceSpan
from .money import parse_hkd


class Evidence(TypedDict):
    source: str
    locator: str
    quote: str
    value: str | float


FIELD_LABELS = {
    "project_title": "項目名稱",
    "objectives": "項目目的",
    "target_users": "服務對象",
    "proposal": "實施方案",
    "requested_budget": "申請預算",
    "timeline": "項目時間表",
    "expected_outcomes": "預期成果",
    "team_members": "團隊成員",
    "contact_email": "聯絡電郵",
}
_ALIASES = {
    "project_title": ("project title", "project name", "title", "項目標題", "專案名稱", "計劃名稱", "項目申請書"),
    "objectives": ("objective", "project objectives", "purpose", "problem", "problem statement", "project overview",
                   "project purpose", "目的", "目標", "項目目標", "項目概述", "項目背景", "項目簡介"),
    "target_users": ("target audience", "users", "beneficiaries", "target beneficiaries", "intended users",
                     "目標用户", "目標羣體", "受惠對象", "受益人羣"),
    "proposal": ("solution", "proposed solution", "technical approach", "approach", "implementation plan",
                 "project plan", "方案", "項目方案", "技術方案", "解決方案", "執行方案", "實施計劃", "項目內容"),
    "requested_budget": ("budget", "requested funding", "requested amount", "budget request", "amount requested",
                          "funding request", "grant amount requested", "grant requested", "total budget",
                          "total requested budget", "total requested funding", "預算", "申請金額", "申請資助額",
                          "申請資助金額", "資助申請額", "預算總額", "總預算", "資助金額", "申請款項"),
    "timeline": ("duration", "project duration", "implementation timeline", "project timeline", "時間表",
                 "項目週期", "實施週期", "項目週期", "實施週期", "進度安排", "時程", "項目期限"),
    "expected_outcomes": ("outcomes", "deliverables", "expected results", "expected impact", "預期成效", "預期產出",
                          "預期結果", "預計成果"),
    "team_members": ("team", "project team", "團隊", "項目團隊"),
    "contact_email": ("email", "email address", "e-mail", "聯絡郵箱", "聯繫郵箱", "聯繫電郵", "聯絡電郵地址",
                      "電子郵件", "電郵", "郵箱"),
}
# Legacy labels remain accepted in applicant-supplied text.
_LEGACY = str.maketrans({
    "項": "\u9879", "稱": "\u79f0", "專": "\u4e13", "計": "\u8ba1", "劃": "\u5212",
    "請": "\u8bf7", "書": "\u4e66", "標": "\u6807", "題": "\u9898", "簡": "\u7b80", "户": "\u6237",
    "務": "\u52a1", "對": "\u5bf9", "體": "\u4f53", "實": "\u5b9e", "術": "\u672f", "決": "\u51b3",
    "執": "\u6267", "內": "\u5185", "預": "\u9884", "總": "\u603b", "資": "\u8d44", "額": "\u989d",
    "幣": "\u5e01", "圓": "\u5706", "時": "\u65f6", "間": "\u95f4", "週": "\u5468", "進": "\u8fdb",
    "產": "\u4ea7", "結": "\u7ed3", "團": "\u56e2", "隊": "\u961f", "員": "\u5458", "聯": "\u8054",
    "絡": "\u7edc", "繫": "\u7cfb", "電": "\u7535", "郵": "\u90ae", "備": "\u5907", "註": "\u6ce8",
})
_HKD_LABEL = re.compile(r"\s*\(\s*(?:HKD|HK\$|港幣|港\u5e01|港元)\s*\)\s*$", re.IGNORECASE)
_LABEL_SEPARATOR = re.compile(r"^([^:：=]{1,70})[:：=](.*)$")
_ROW = re.compile(r"^(.*?)第 \d+ 列$")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_COMMON_HEADERS = {"item", "name", "notes", "note", "remarks", "description", "value", "status",
                   "項目", "名稱", "説明", "備註", "金額", "值"}
_BUDGET_PHRASES = (
    "requested budget", "requested funding", "total budget", "total requested budget", "amount requested",
    "申請預算", "申請資助額", "申請資助金額", "申請金額", "預算總額", "總預算",
)
_BUDGET_CLAUSE = re.compile(
    "(?:" + "|".join(re.escape(label) for phrase in _BUDGET_PHRASES
                     for label in dict.fromkeys((phrase.translate(_LEGACY), phrase)))
    + r"|\bwe (?:are requesting|request))\s*(?:為|\u4e3a|\bis\b|\btotals\b)?\s+(.*)$"
    + r"|(?:申請預算|申請資助額|申請金額|預算總額|總預算|\u7533\u8bf7\u9884算|\u7533\u8bf7\u91d1\u989d)"
    + r"\s*(?:為|\u4e3a)\s*(.*)$",
    re.IGNORECASE,
)


def _normalize_label(label: str) -> str:
    label = unicodedata.normalize("NFKC", label).strip(" *_`#\t")
    return re.sub(r"[\s_-]+", " ", _HKD_LABEL.sub("", label)).strip().casefold()


_FIELD_BY_LABEL = {
    _normalize_label(spelling): key
    for key, names in _ALIASES.items()
    for label in (key, FIELD_LABELS[key], *names)
    for spelling in (label, label.translate(_LEGACY))
}
_KNOWN_HEADERS = _COMMON_HEADERS | {label.translate(_LEGACY) for label in _COMMON_HEADERS}


def _line_field(line: str) -> tuple[str | None, str]:
    cleaned = line.lstrip(" #-*>\t")
    match = _LABEL_SEPARATOR.fullmatch(cleaned)
    label, value = match.groups() if match else (cleaned, "")
    return _FIELD_BY_LABEL.get(_normalize_label(label)), value.strip(" *_`\t")


def _value(key: str, text: str) -> str | float | None:
    if key == "requested_budget":
        return parse_hkd(text.strip().rstrip("。；;."))
    if key == "contact_email":
        return text.casefold() if _EMAIL.fullmatch(text) else None
    return " ".join(text.split()) or None


def source_spans(doc: Document) -> Iterator[EvidenceSpan]:
    if doc.segments:
        yield from doc.segments
    else:
        unit = "列" if doc.doc_type in (DocType.CSV, DocType.XLSX) else "行"
        for number, line in enumerate(doc.text.splitlines(), 1):
            yield EvidenceSpan(f"第 {number} {unit}", line)


def _table_fields(line: str, header: dict[int, str]) -> Iterator[tuple[str, str]]:
    cells = [cell.strip() for cell in line.split(" | ")]
    labels = [_normalize_label(cell) for cell in cells]
    keys = [_FIELD_BY_LABEL.get(label) for label in labels]
    if any(keys) and all(not label or key or label in _KNOWN_HEADERS for label, key in zip(labels, keys)):
        header.clear()
        header.update((index, key) for index, key in enumerate(keys) if key)
        return
    for index, key in header.items():
        if index < len(cells) and not keys[index]:
            yield key, cells[index]
    for index, key in enumerate(keys[:-1]):
        if key and not keys[index + 1]:
            yield key, cells[index + 1]


def _explicit_budget(line: str) -> str:
    match = _BUDGET_CLAUSE.search(line)
    if not match:
        return ""
    amount = next(part for part in match.groups() if part is not None)
    amount = re.split(r"[。；;，]|,\s", amount, maxsplit=1)[0]
    return re.sub(r"\s+(?:in funding|in grant funding)\.?$", "", amount, flags=re.IGNORECASE)


def document_evidence(doc: Document) -> Iterator[tuple[str, Evidence]]:
    lines = [EvidenceSpan(span.locator, line.strip())
             for span in source_spans(doc) for line in span.text.splitlines() or [""]]
    tables: dict[str, dict[int, str]] = {}
    for index, span in enumerate(lines):
        locator, quote = span.locator, span.text
        if not quote:
            continue
        row = _ROW.fullmatch(span.locator)
        if row:
            pairs = _table_fields(quote, tables.setdefault(row[1], {}))
        else:
            key, raw = _line_field(quote)
            if key and not raw:
                neighbors = lines[index + 1:index + 6]
                if neighbors and not neighbors[0].text:
                    neighbors = neighbors[1:]
                content = []
                for line in neighbors:
                    if (not line.text or line.text.startswith(("#", "- ", "* ")) or _ROW.fullmatch(line.locator)
                            or _line_field(line.text)[0] or _LABEL_SEPARATOR.fullmatch(line.text)):
                        break
                    content.append(line)
                if content:
                    locator = content[0].locator
                    quote = raw = "\n".join(line.text for line in content)
            if not key:
                raw = _explicit_budget(quote)
                key = "requested_budget" if raw else None
            pairs = [(key, raw)] if key else []
        for key, raw in pairs:
            value = _value(key, raw)
            if value is not None:
                yield key, {"source": doc.name, "locator": locator, "quote": quote, "value": value}
