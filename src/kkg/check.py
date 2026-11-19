"""完整性 & 合规性检查 Agent：按计划规则做必填资料、附件、格式、资格、利益冲突审查。"""
from __future__ import annotations

import re
from typing import Any

from .models import Application, Finding, FindingType, Severity
from .program import Program

_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.]+$")


def check_completeness(app: Application, prog: Program) -> list[Finding]:
    findings: list[Finding] = []

    # 1. 必填字段
    for rf in prog.required_fields:
        val = app.fields.get(rf.key)
        label = rf.label or rf.key
        if val is None or (isinstance(val, str) and not val.strip()):
            findings.append(Finding(FindingType.MISSING, Severity.ERROR,
                                    f"必填字段缺失：{label}", source="required_fields", needs_human=True))
            continue
        if rf.ftype == "email" and not _EMAIL_RE.match(str(val)):
            findings.append(Finding(FindingType.FORMAT, Severity.WARNING,
                                    f"字段格式异常：{label} 不是有效邮箱（{val}）",
                                    source="required_fields"))
        if rf.ftype == "number":
            try:
                num = float(str(val).replace(",", ""))
                if rf.min_value is not None and num < rf.min_value:
                    findings.append(Finding(FindingType.ANOMALY, Severity.WARNING,
                                            f"{label}={num} 低于下限 {rf.min_value}", source="range_check"))
                if rf.max_value is not None and num > rf.max_value:
                    findings.append(Finding(FindingType.ANOMALY, Severity.WARNING,
                                            f"{label}={num} 超出上限 {rf.max_value}", source="range_check"))
            except ValueError:
                findings.append(Finding(FindingType.FORMAT, Severity.WARNING,
                                        f"{label} 应为数字，实际为「{val}」", source="type_check"))

    # 2. 必备附件（按文件名关键词匹配）
    names = [d.name.lower() for d in app.docs]
    for rd in prog.required_docs:
        if not any(rd.name.lower() in n for n in names):
            sev = Severity.ERROR if rd.required else Severity.INFO
            findings.append(Finding(FindingType.MISSING, sev,
                                    f"{'必备' if rd.required else '建议'}附件缺失：{rd.name}",
                                    source="required_docs", needs_human=rd.required))

    # 3. 解析失败 / 空文档
    for d in app.docs:
        if d.error:
            findings.append(Finding(FindingType.FORMAT, Severity.WARNING,
                                    f"附件「{d.name}」无法解析（{d.error}），需人工查看",
                                    source=d.name, needs_human=True))

    # 4. 资格
    elig = prog.eligibility
    n_team = len(app.team) or 1
    if not (elig.min_team <= n_team <= elig.max_team):
        findings.append(Finding(FindingType.ELIGIBILITY, Severity.ERROR,
                                f"团队人数 {n_team} 不在允许范围 {elig.min_team}–{elig.max_team}",
                                source="eligibility", needs_human=True))
    blob = (app.title + " " + " ".join(str(v) for v in app.fields.values())).lower()
    for kw in elig.excluded_keywords:
        if kw.lower() in blob:
            findings.append(Finding(FindingType.ELIGIBILITY, Severity.CRITICAL,
                                    f"触发排除关键词「{kw}」，疑似不符合申请资格",
                                    source="eligibility", needs_human=True))

    # 5. 预算上限
    if prog.budget_cap is not None:
        budget = _to_number(app.fields.get("budget"))
        if budget is not None and budget > prog.budget_cap:
            findings.append(Finding(FindingType.ANOMALY, Severity.WARNING,
                                    f"预算 {budget:,.0f} 超出计划上限 {prog.budget_cap:,.0f}",
                                    source="budget_cap", needs_human=True))

    # 6. 潜在利益冲突（评审人 ↔ 申请全文关键词：表单字段 + 附件正文）
    full_blob = (app.title + " " + " ".join(str(v) for v in app.fields.values())
                 + " " + " ".join(d.text for d in app.docs if d.text)).lower()
    for reviewer, kws in prog.conflict_rules.items():
        for kw in kws:
            if kw.lower() in full_blob:
                findings.append(Finding(FindingType.CONFLICT, Severity.CRITICAL,
                                        f"潜在利益冲突：评审人「{reviewer}」与申请中出现的关键词「{kw}」相关，"
                                        f"建议更换评审人", source="conflict_check", needs_human=True))

    return findings


def detect_duplicates(apps: list[Application]) -> list[Finding]:
    """跨申请查重：同邮箱/同标题/同申请人视为疑似重复。"""
    findings: list[Finding] = []
    seen: dict[str, list[str]] = {}
    for a in apps:
        for key in filter(None, [a.email.lower(), a.title.lower(), a.applicant.lower()]):
            seen.setdefault(key, []).append(a.app_id)
    for key, ids in seen.items():
        if len(set(ids)) > 1:
            findings.append(Finding(FindingType.DUPLICATE, Severity.WARNING,
                                    f"疑似重复申请：标识「{key[:60]}」出现在 {sorted(set(ids))}",
                                    source="dedup", needs_human=True))
    return findings


def _to_number(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace("HK$", "").replace("$", "").strip())
    except ValueError:
        return None
