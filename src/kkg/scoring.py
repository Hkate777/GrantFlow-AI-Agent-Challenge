"""確定性評分與權重校驗：零 LLM、零新數字、可重現。

與 review.py 的關係：review.aggregate() 負責完整的加權總分與分歧分析；
本模組提供兩項它沒有明確對外保證的能力——

1. **權重和校驗**：strict 模式要求權重和必須為 1.0（照搬「weights 和必須
   為 1.0」的約束）；relative 模式接受現有配置的相對權重（如 3/3/2/2），
   並明確回傳歸一化結果，避免「以為是百分比卻其實是比例」的誤解。
2. **逐步可解釋的確定性評分**：每一步的分子、分母、加權貢獻都列出，
   全部由輸入分數與權重算出，不呼叫模型、不引入新數字。

缺失值一律以 None 呈現並列入 missing 清單，絶不靜默當零分填入。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .program import Program

_WEIGHT_TOLERANCE = 1e-6


@dataclass(frozen=True)
class WeightCheck:
    """權重校驗結果。"""

    mode: str                       # strict / relative
    ok: bool
    total: float
    normalized: dict[str, float] = field(default_factory=dict)  # 和為 1.0 的權重
    issues: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        if self.ok:
            return f"權重校驗通過（{self.mode} 模式，權重和 {self.total:g}）"
        return "權重校驗失敗：" + "；".join(self.issues)


def validate_weights(weights: dict[str, float], *, mode: str = "strict") -> WeightCheck:
    """校驗權重。strict 要求和為 1.0；relative 接受任意正向比例並歸一化。

    兩種模式都拒絕非有限數、負數與全零——全零代表配置錯誤，不是合法權重。
    """
    if mode not in {"strict", "relative"}:
        raise ValueError("mode 必須是 strict 或 relative。")
    issues: list[str] = []
    if not weights:
        return WeightCheck(mode, False, 0.0, {}, ("維度權重不可為空。",))
    for name, value in weights.items():
        if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
            issues.append(f"維度「{name}」的權重不是有效非負數。")
    if issues:
        return WeightCheck(mode, False, float("nan"), {}, tuple(issues))
    total = float(sum(weights.values()))
    if total <= 0:
        return WeightCheck(mode, False, total, {}, ("權重總和必須大於零。",))
    normalized = {name: value / total for name, value in weights.items()}
    if mode == "strict" and abs(total - 1.0) > _WEIGHT_TOLERANCE:
        issues.append(f"權重和必須為 1.0，實際為 {total:g}；可改用 relative 模式或先歸一化。")
    return WeightCheck(mode, not issues, total, normalized, tuple(issues))


def validate_program_weights(program: Program, *, mode: str = "relative") -> WeightCheck:
    """對Program 的評審維度做權重校驗；預設 relative 以相容現有配置。"""
    return validate_weights({rule.name: rule.weight for rule in program.criteria}, mode=mode)


@dataclass(frozen=True)
class ScoreStep:
    """單一維度的確定性評分步驟。"""

    name: str
    score: float | None             # 原始分；缺失為 None
    max_score: float
    weight: float                   # 和為 1.0 的歸一化權重
    contribution: float | None      # 百分制貢獻；缺失為 None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "score": self.score, "max_score": self.max_score,
            "weight": self.weight, "contribution": self.contribution,
        }


@dataclass(frozen=True)
class DeterministicScore:
    """確定性評分結果：總分與每一步，全部可追溯、可重現。"""

    total: float | None
    steps: tuple[ScoreStep, ...]
    missing: tuple[str, ...]
    weights: WeightCheck

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "steps": [step.as_dict() for step in self.steps],
            "missing": list(self.missing),
            "weights": {
                "mode": self.weights.mode, "ok": self.weights.ok,
                "total": self.weights.total, "issues": list(self.weights.issues),
            },
        }

    @property
    def complete(self) -> bool:
        return self.total is not None and not self.missing


def deterministic_score(scores: dict[str, float], program: Program, *,
                        mode: str = "relative") -> DeterministicScore:
    """按歸一化權重計算百分制總分；缺維度時總分為 None 並列於 missing。

    與 review.aggregate() 的差別：本函式不做範圍校正或預設補零，缺一個維度
    就明確回報不完整，避免「用零分冒充已評分」的隱性問題。
    """
    weights = validate_program_weights(program, mode=mode)
    if not weights.ok:
        return DeterministicScore(None, (), tuple(sorted(scores)), weights)
    steps: list[ScoreStep] = []
    missing: list[str] = []
    for rule in program.criteria:
        raw = scores.get(rule.name)
        if raw is None or type(raw) not in {int, float} or not math.isfinite(raw):
            missing.append(rule.name)
            steps.append(ScoreStep(rule.name, None, rule.max_score,
                                   weights.normalized[rule.name], None))
            continue
        contribution = (raw / rule.max_score) * 100 * weights.normalized[rule.name]
        steps.append(ScoreStep(rule.name, raw, rule.max_score,
                               weights.normalized[rule.name], round(contribution, 4)))
    # 只要有缺失維度就不給總分——這正是「缺失值不偷偷填」的落實。
    total = None if missing else round(sum(step.contribution or 0 for step in steps), 2)
    return DeterministicScore(total, tuple(steps), tuple(missing), weights)


def score_markdown(result: DeterministicScore) -> str:
    """把確定性評分渲染為可稽核的 Markdown 表格。"""
    lines = [
        "# 確定性評分明細",
        "",
        f"- 權重模式：{result.weights.mode}",
        f"- 總分：{result.total if result.total is not None else '不完整（缺維度）'}",
        "",
        "| 維度 | 原始分 | 滿分 | 歸一化權重 | 百分制貢獻 |",
        "|---|---:|---:|---:|---:|",
    ]
    for step in result.steps:
        score = step.score if step.score is not None else "缺失"
        contribution = step.contribution if step.contribution is not None else "缺失"
        lines.append(f"| {step.name} | {score} | {step.max_score} | "
                     f"{step.weight:.4f} | {contribution} |")
    if result.missing:
        lines.append("")
        lines.append("缺失維度（未計分，也未補零）：" + "、".join(result.missing))
    if result.weights.issues:
        lines.append("")
        lines.append("權重提示：" + "；".join(result.weights.issues))
    return "\n".join(lines)
