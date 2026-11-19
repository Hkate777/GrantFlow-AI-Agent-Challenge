"""評審彙總Agent：彙總評分與評語、識別分歧、生成結果通知與後續待辦。

融合說明（codex/web-workbench-merge）：
- 保留 main 線的加權與分歧閾值邏輯（divergence_threshold、per_criterion 結構）。
- 加回工作台線的評分校驗（重複評審員/範圍/維度匹配 → validation_errors）、
  assess() 的 holds/status 狀態機與 review_draft() 評審進度草稿，
  供 workspace 的評分、複議與人工簽發流程使用。
"""
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
    "insufficient_reviews": "評審人數不足",
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
    """按計劃維度權重計算加權總分；先校驗評分有效性，再識別評分差異過大的項目與維度。"""
    scores, validation_errors = _validate_scores(scores, prog)
    comments = [{"reviewer": score.reviewer, "comment": score.comment} for score in scores]
    if not scores or validation_errors:
        return {"n_reviewers": len(scores), "weighted_total": None, "spread": None,
                "per_reviewer_weighted": {}, "per_criterion": {}, "flags": [],
                "comments": comments, "validation_errors": validation_errors}

    weights = {c.name: c.weight for c in prog.criteria}
    max_scores = {c.name: c.max_score for c in prog.criteria}
    total_w = sum(weights.values()) or 1.0

    per_crit: dict[str, dict] = {}
    for name in weights:
        vals = [s.criteria.get(name, 0.0) for s in scores]
        per_crit[name] = {
            "scores": vals,
            "mean": round(mean(vals), 2),
            "stdev": round(pstdev(vals), 2),
            "max": max_scores[name],
        }

    def _weighted(s: ReviewScore) -> float:
        # 歸一到百分制
        acc = 0.0
        for name, w in weights.items():
            m = max_scores.get(name, 25) or 25
            acc += (s.criteria.get(name, 0.0) / m) * 100 * w
        return acc / total_w

    weighted = [round(_weighted(s), 2) for s in scores]
    spread = max(weighted) - min(weighted)

    flags: list[str] = []
    if spread >= prog.divergence_threshold:
        flags.append(
            f"評審分歧顯著：最高{max(weighted)} vs最低{min(weighted)}，"
            f"差值{spread:.1f} ≥ 閾值{prog.divergence_threshold}，需安排複議討論。")
    for name, st in per_crit.items():
        if st["stdev"] > st["max"] * 0.25:
            flags.append(f"維度「{name}」評分波動大（標準差{st['stdev']:.1f}），建議複核該維度評分依據。")

    return {
        "n_reviewers": len(scores),
        "per_reviewer_weighted": dict(zip([s.reviewer for s in scores], weighted)),
        "weighted_total": round(mean(weighted), 2),
        "spread": round(spread, 2),
        "per_criterion": per_crit,
        "flags": flags,
        "comments": comments,
        "validation_errors": validation_errors,
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
    # 無有效評分時，首要狀態是待評分/待更正——排在人工覆核之前，
    # 否則僅含提示級發現的新案會被誤標為待人工覆核。
    if result["validation_errors"]:
        holds.append("invalid_scores")
    elif not scores:
        holds.append("pending_scores")
    elif len(scores) < program.min_reviewers:
        holds.append("insufficient_reviews")
    if active_conflicts or (not reviewed and any(finding.needs_human for finding in findings)):
        holds.append("needs_review")

    total = result["weighted_total"]
    return {**result, "holds": holds, "status": holds[0] if holds else "awaiting_decision",
            "approval_allowed": not holds and total is not None and total >= pass_threshold,
            "decline_allowed": not holds or (reviewed and "needs_compliance" in holds and not active_conflicts),
            "active_conflicts": active_conflicts, "notice": None}


def result_notice(app_id: str, title: str, agg: dict, passed: bool) -> str:
    head = "通過" if passed else "未通過"
    lines = [
        f"# 評審結果通知 · {app_id}",
        "",
        f"申請項目「{title}」評審完成：{head}。",
        f"- 加權總分（百分制）：{agg.get('weighted_total', 'N/A')}",
        f"- 評審人數：{agg.get('n_reviewers', 0)}",
    ]
    if agg.get("flags"):
        lines += ["", "## 備註"] + [f"- {f}" for f in agg["flags"]]
    return "\n".join(lines)


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
    if result.get("status") in {"approved", "declined"}:
        todos = ["歸檔評審資料包與審計日誌"]
        if result["status"] == "approved":
            todos.append("建立項目追蹤台賬（里程碑/資助發放/定期報告）")
        todos.append(f"[{app_id}] 按核准流程發送已簽發的評審結果通知")
        return todos
    holds = result.get("holds") or []
    todos = [STATUS_LABELS[status] for status in holds] or ["由負責人確認及簽發結果"]
    todos += [f"安排 {reviewer} 迴避並更正評分" for reviewer in result.get("active_conflicts", [])]
    if result.get("flags"):
        todos.append(f"[{app_id}] 組織複議會議：處理評分分歧（{len(result['flags'])}項）")
    todos.append(f"[{app_id}] 歸檔評審資料包與審計日誌")
    return [f"[{app_id}] {task}" for task in todos]
