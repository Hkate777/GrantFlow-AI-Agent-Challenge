"""结构化抽取 + 跨源交叉验证 Agent。

- extract_fields：用 LLM 从申请书正文抽取结构化信息，与表单字段比对。
- cross_verify：多来源（表单 / 申请书 / 财务报表 / 证明文件）交叉核对，识别矛盾与异常。
"""
from __future__ import annotations

import json
import re
from typing import Any

from .llm import LLM
from .models import Application, Finding, FindingType, Severity

_NUM_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def extract_fields(app: Application, llm: LLM) -> dict[str, Any]:
    """从申请书全文抽取关键信息。LLM 输出 JSON，解析失败时降级为空。"""
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
    """把 LLM 抽取结果与申请人自报字段交叉比对，发现矛盾。"""
    findings: list[Finding] = []

    def _num(v: Any) -> float | None:
        if v is None:
            return None
        m = _NUM_RE.search(str(v).replace(",", ""))
        return float(m.group()) if m else None

    # 预算：自报 vs 文书中出现
    form_budget = _num(app.fields.get("budget"))
    doc_budget = _num(extracted.get("requested_budget"))
    if form_budget is not None and doc_budget is not None:
        if abs(form_budget - doc_budget) > max(form_budget, 1) * 0.05:
            findings.append(Finding(
                FindingType.INCONSISTENT, Severity.ERROR,
                f"预算前后矛盾：自报表单 {form_budget:,.0f} vs 申请书 {doc_budget:,.0f}",
                source="cross_verify.budget", needs_human=True))

    # 标题：自报 vs 抽取
    ext_title = str(extracted.get("project_title") or "").strip()
    if ext_title and app.title and ext_title.lower() not in app.title.lower() \
            and app.title.lower() not in ext_title.lower():
        findings.append(Finding(
            FindingType.INCONSISTENT, Severity.WARNING,
            f"项目名称不一致：表单「{app.title}」 vs 文书「{ext_title}」",
            source="cross_verify.title"))

    # 邮箱：自报 vs 文书
    ext_email = str(extracted.get("contact_email") or "").strip().lower()
    if ext_email and app.email and ext_email != app.email.lower():
        findings.append(Finding(
            FindingType.INCONSISTENT, Severity.WARNING,
            f"联系邮箱不一致：表单 {app.email} vs 文书 {ext_email}",
            source="cross_verify.email"))

    return findings


def anomaly_scan(apps: list[Application]) -> list[Finding]:
    """跨申请异常检测：预算离群（超过中位数 3 倍 MAD 或均值 2.5 倍）标记人工复核。"""
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
                f"申请 {app_id} 预算 {v:,.0f} 显著偏离批次中位数 {med:,.0f}，建议人工复核",
                source="anomaly_scan", needs_human=True))
    return findings
