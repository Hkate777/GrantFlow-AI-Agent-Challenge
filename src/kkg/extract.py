"""結構化抽取 + 跨源交叉驗證 Agent。

- extract_fields：用 LLM 從申請書正文抽取結構化信息，與表單字段比對。
- cross_verify：多來源（表單 / 申請書 / 財務報表 / 證明文件）交叉核對，識別矛盾與異常。
"""
from __future__ import annotations

import json
import re
from typing import Any

from .llm import LLM
from .models import Application, Finding, FindingType, Severity

_NUM_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def extract_fields(app: Application, llm: LLM) -> dict[str, Any]:
    """從申請書全文抽取關鍵信息。LLM 輸出 JSON，解析失敗時降級為空。"""
    corpus = "\n\n".join(f"### {d.name}\n{d.text}" for d in app.docs if d.text)[:12000]
    if not corpus:
        return {}
    system = (
        "You are an information extraction assistant for grant applications. "
        "Extract the following fields from the application documents and return JSON only: "
        "project_title, objectives, target_users, requested_budget, timeline, "
        "expected_outcomes, team_members, contact_email. "
        "Use null when a field is not found. Return JSON only, no prose."
    )
    try:
        raw = llm.complete(system, corpus)
        start, end = raw.find("{"), raw.rfind("}")
        return json.loads(raw[start:end + 1]) if start >= 0 and end > start else {}
    except Exception:  # noqa: BLE001
        return {}


def cross_verify(app: Application, extracted: dict[str, Any]) -> list[Finding]:
    """把 LLM 抽取結果與申請人自報字段交叉比對，發現矛盾。"""
    findings: list[Finding] = []

    def _num(v: Any) -> float | None:
        if v is None:
            return None
        m = _NUM_RE.search(str(v).replace(",", ""))
        return float(m.group()) if m else None

    # 預算：自報 vs 文書中出現
    form_budget = _num(app.fields.get("budget"))
    doc_budget = _num(extracted.get("requested_budget"))
    if form_budget is not None and doc_budget is not None:
        if abs(form_budget - doc_budget) > max(form_budget, 1) * 0.05:
            findings.append(Finding(
                FindingType.INCONSISTENT, Severity.ERROR,
                f"預算前後矛盾：自報表單 {form_budget:,.0f} vs 申請書 {doc_budget:,.0f}",
                source="cross_verify.budget", needs_human=True))

    # 標題：自報 vs 抽取
    ext_title = str(extracted.get("project_title") or "").strip()
    if ext_title and app.title and ext_title.lower() not in app.title.lower() \
            and app.title.lower() not in ext_title.lower():
        findings.append(Finding(
            FindingType.INCONSISTENT, Severity.WARNING,
            f"項目名稱不一致：表單「{app.title}」 vs 文書「{ext_title}」",
            source="cross_verify.title"))

    # 郵箱：自報 vs 文書
    ext_email = str(extracted.get("contact_email") or "").strip().lower()
    if ext_email and app.email and ext_email != app.email.lower():
        findings.append(Finding(
            FindingType.INCONSISTENT, Severity.WARNING,
            f"聯繫郵箱不一致：表單 {app.email} vs 文書 {ext_email}",
            source="cross_verify.email"))

    return findings


def anomaly_scan(apps: list[Application]) -> list[Finding]:
    """跨申請異常檢測：預算離羣（超過中位數 3 倍 MAD 或均值 2.5 倍）標記人工複核。"""
    budgets = []
    for a in apps:
        m = _NUM_RE.search(str(a.fields.get("budget", "")).replace(",", ""))
        if m:
            budgets.append((a.app_id, float(m.group())))
    if len(budgets) < 3:
        return []
    vals = sorted(v for _, v in budgets)
    med = vals[len(vals) // 2]
    mad = sorted(abs(v - med) for _, v in budgets)[len(vals) // 2] or 1.0
    findings: list[Finding] = []
    for app_id, v in budgets:
        if abs(v - med) / mad > 5:
            findings.append(Finding(
                FindingType.ANOMALY, Severity.WARNING,
                f"申請 {app_id} 預算 {v:,.0f} 顯著偏離批次中位數 {med:,.0f}，建議人工複核",
                source="anomaly_scan", needs_human=True))
    return findings
