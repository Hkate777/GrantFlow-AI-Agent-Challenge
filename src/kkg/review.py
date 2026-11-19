"""評審彙總Agent：彙總評分與評語、識別分歧、生成結果通知與後續待辦。"""
from __future__ import annotations

from statistics import mean, pstdev

from .models import ReviewScore
from .program import Program


def aggregate(scores: list[ReviewScore], prog: Program) -> dict:
    """按計劃維度權重計算加權總分；識別評分差異過大的項目與維度。"""
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
        "comments": [{"reviewer": s.reviewer, "comment": s.comment} for s in scores],
    }


def result_notice(app_id: str, title: str, agg: dict, passed: bool) -> str:
    head = "[通過] 通過" if passed else "[未通過] 未通過"
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


def followup_todos(app_id: str, agg: dict) -> list[str]:
    todos = [f"[{app_id}] 向申請人發送評審結果通知"]
    if agg.get("flags"):
        todos.append(f"[{app_id}] 組織複議會議：處理評分分歧（{len(agg['flags'])}項）")
    todos += [
        f"[{app_id}] 歸檔評審資料包與審計日誌",
        f"[{app_id}] 建立項目追蹤台賬（里程碑/資助發放/定期報告）",
    ]
    return todos
