"""核驗評分並整理可簽發的評審結論。"""
from __future__ import annotations

import math
import unicodedata
from statistics import mean, pstdev

from .models import Finding, FindingType, ReviewScore, Severity
from .program import Program


STATUS_LABELS = {
    "needs_materials": "待補件",
    "needs_compliance": "資格待核",
    "needs_review": "待人工覆核",
    "pending_scores": "待評分",
    "invalid_scores": "評分待更正",
    "pending_deliberation": "待複議",
    "awaiting_decision": "待簽發",
    "approved": "已通過",
    "declined": "未通過",
}

# Preserve the names used by older review exports under the Hong Kong rubric.
_LEGACY_LABELS = {
    "\u521b\u65b0\u6027": "創新性",
    "\u56e2\u961f\u80fd\u529b": "團隊能力",
    "\u793e\u4f1a\u4ef7\u503c": "社會價值",
}


def _label(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    return _LEGACY_LABELS.get(normalized, normalized)


def _validate_scores(scores: list[ReviewScore], prog: Program) -> tuple[list[ReviewScore], list[str]]:
    if scores and not prog.criteria:
        return [], ["計劃尚未設定評審維度。"]
    criteria = {_label(rule.name): rule for rule in prog.criteria}
    reviewers: set[str] = set()
    valid: list[ReviewScore] = []
    errors: list[str] = []
    for score in scores:
        reviewer = score.reviewer.strip() if isinstance(score.reviewer, str) else ""
        key = reviewer.casefold()
        if not reviewer or key in reviewers:
            errors.append(f"評審員名稱缺失或重複：{reviewer or '未提供'}。")
            continue
        reviewers.add(key)
        if not isinstance(score.criteria, dict) or any(not isinstance(name, str) for name in score.criteria):
            errors.append(f"{reviewer} 的評分維度格式有誤。")
            continue
        values = {_label(name): value for name, value in score.criteria.items()}
        if values.keys() != criteria.keys() or len(values) != len(score.criteria):
            errors.append(f"{reviewer} 的評分維度缺失、重複或與計劃不符。")
            continue
        invalid = [name for name, value in values.items()
                   if type(value) not in {int, float} or not 0 <= value <= criteria[name].max_score
                   or not math.isfinite(value)]
        if invalid:
            errors.append(f"{reviewer} 的評分超出範圍：{'、'.join(invalid)}。")
            continue
        valid.append(ReviewScore(reviewer, {criteria[name].name: value for name, value in values.items()},
                                 score.comment if isinstance(score.comment, str) else ""))
    return valid, errors


def aggregate(scores: list[ReviewScore], prog: Program) -> dict:
    scores, errors = _validate_scores(scores, prog)
    comments = [{"reviewer": score.reviewer, "comment": score.comment} for score in scores]
    if not scores or errors:
        return {"n_reviewers": len(scores), "weighted_total": None, "spread": None,
                "per_reviewer_weighted": {}, "per_criterion": {}, "flags": [],
                "comments": comments, "validation_errors": errors}

    total_weight = sum(rule.weight for rule in prog.criteria)
    per_criterion = {}
    for rule in prog.criteria:
        values = [score.criteria[rule.name] for score in scores]
        per_criterion[rule.name] = {
            "scores": values,
            "mean": round(mean(values), 2),
            "stdev": round(pstdev(values), 2),
            "max": rule.max_score,
        }
    weighted = [
        round(100 * sum((score.criteria[rule.name] / rule.max_score) * (rule.weight / total_weight)
                        for rule in prog.criteria), 2)
        for score in scores
    ]
    spread = round(max(weighted) - min(weighted), 2)
    flags = []
    if spread and spread >= prog.divergence_threshold:
        flags.append(f"評審分歧顯著：最高 {max(weighted)}，最低 {min(weighted)}，"
                     f"相差 {spread:.1f} 分，須安排複議。")
    for name, result in per_criterion.items():
        if result["stdev"] > result["max"] * 0.25:
            flags.append(f"「{name}」評分波動較大，建議核對評分依據。")
    return {
        "n_reviewers": len(scores),
        "per_reviewer_weighted": dict(zip([score.reviewer for score in scores], weighted)),
        "weighted_total": round(mean(weighted), 2),
        "spread": spread,
        "per_criterion": per_criterion,
        "flags": flags,
        "comments": comments,
        "validation_errors": [],
    }


def assess(scores: list[ReviewScore], program: Program, findings: list[Finding],
           *, pass_threshold: float, reviewed: bool = False) -> dict:
    result = aggregate(scores, program)
    serious = {finding.finding_type for finding in findings
               if finding.severity in {Severity.ERROR, Severity.CRITICAL}}
    conflicted = {finding.detail.casefold() for finding in findings
                  if finding.finding_type == FindingType.CONFLICT and finding.detail}
    scored_by = {score.reviewer.strip().casefold() for score in scores if isinstance(score.reviewer, str)}
    active_conflicts = sorted(conflicted & scored_by)
    holds = []
    if result["flags"] and not reviewed:
        holds.append("pending_deliberation")
    if serious & {FindingType.MISSING, FindingType.FORMAT}:
        holds.append("needs_materials")
    if FindingType.ELIGIBILITY in serious:
        holds.append("needs_compliance")
    if active_conflicts or (not reviewed and any(finding.needs_human for finding in findings)):
        holds.append("needs_review")
    if result["validation_errors"]:
        holds.append("invalid_scores")
    elif not scores:
        holds.append("pending_scores")

    total = result["weighted_total"]
    return {**result, "holds": holds, "status": holds[0] if holds else "awaiting_decision",
            "approval_allowed": not holds and total is not None and total >= pass_threshold,
            "decline_allowed": not holds or (reviewed and "needs_compliance" in holds and not active_conflicts),
            "active_conflicts": active_conflicts, "notice": None}


def result_notice(app_id: str, title: str, agg: dict, passed: bool) -> str:
    score = agg.get("weighted_total")
    return "\n".join([
        f"# 評審結果通知 · {app_id}",
        "",
        f"申請項目「{title}」評審結果為 **{'通過' if passed else '未通過'}**。",
        f"- 加權總分（百分制）：{score if score is not None else '未評分'}",
        f"- 評審人數：{agg.get('n_reviewers', 0)}",
    ])


def review_draft(app_id: str, title: str, result: dict) -> str:
    score = result["weighted_total"]
    lines = [f"# 評審進度 · {app_id}", "", f"項目：{title}",
             f"狀態：**{STATUS_LABELS[result['status']]}**。結果通知尚未簽發。"]
    if score is not None:
        lines.append(f"加權總分（百分制）：{score:.2f}")
    lines.extend(f"- {STATUS_LABELS[hold]}" for hold in result["holds"])
    lines.extend(f"- {flag}" for flag in result["flags"])
    lines.extend(f"- {error}" for error in result["validation_errors"])
    return "\n".join(lines) + "\n"


def followup_todos(app_id: str, result: dict) -> list[str]:
    if result["status"] in {"approved", "declined"}:
        tasks = ["按核准流程發送已簽發的評審結果通知", "歸檔評審資料與審計記錄"]
        if result["status"] == "approved":
            tasks.append("登記里程碑、付款計劃與定期報告")
    else:
        tasks = [STATUS_LABELS[status] for status in result["holds"]] or ["由負責人確認及簽發結果"]
        tasks.extend(f"安排 {reviewer} 迴避並更正評分" for reviewer in result["active_conflicts"])
    return [f"[{app_id}] {task}" for task in tasks]
