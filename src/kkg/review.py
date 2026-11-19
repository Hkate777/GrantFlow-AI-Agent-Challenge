"""评审汇总 Agent：汇总评分与评语、识别分歧、生成结果通知与后续待办。"""
from __future__ import annotations

from statistics import mean, pstdev

from .models import ReviewScore
from .program import Program


def aggregate(scores: list[ReviewScore], prog: Program) -> dict:
    """按计划维度权重计算加权总分；识别评分差异过大的项目与维度。"""
    if not scores:
        return {"n_reviewers": 0, "weighted_total": None, "per_criterion": {}, "flags": []}

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
        # 归一到百分制
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
            f"评审分歧显著：最高 {max(weighted)} vs 最低 {min(weighted)}，"
            f"差值 {spread:.1f} ≥ 阈值 {prog.divergence_threshold}，需安排复议讨论。")
    for name, st in per_crit.items():
        if st["stdev"] > st["max"] * 0.25:
            flags.append(f"维度「{name}」评分波动大（标准差 {st['stdev']:.1f}），建议复核该维度评分依据。")

    return {
        "n_reviewers": len(scores),
        "per_reviewer_weighted": dict(zip([s.reviewer for s in scores], weighted)),
        "weighted_total": round(mean(weighted), 2),
        "spread": round(spread, 2),
        "per_criterion": per_crit,
        "flags": flags,
        "comments": [{"reviewer": s.reviewer, "comment": s.comment} for s in scores],
    }


def result_notice(app_id: str, title: str, agg: dict, passed: bool) -> str:
    head = "✅ 通过" if passed else "❌ 未通过"
    lines = [
        f"# 评审结果通知 · {app_id}",
        "",
        f"申请项目「{title}」评审完成：{head}。",
        f"- 加权总分（百分制）：{agg.get('weighted_total', 'N/A')}",
        f"- 评审人数：{agg.get('n_reviewers', 0)}",
    ]
    if agg.get("flags"):
        lines += ["", "## 备注"] + [f"- {f}" for f in agg["flags"]]
    return "\n".join(lines)


def followup_todos(app_id: str, agg: dict) -> list[str]:
    todos = [f"[{app_id}] 向申请人发送评审结果通知"]
    if agg.get("flags"):
        todos.append(f"[{app_id}] 组织复议会议：处理评分分歧（{len(agg['flags'])} 项）")
    todos += [
        f"[{app_id}] 归档评审资料包与审计日志",
        f"[{app_id}] 建立项目追踪台账（里程碑/资助发放/定期报告）",
    ]
    return todos
