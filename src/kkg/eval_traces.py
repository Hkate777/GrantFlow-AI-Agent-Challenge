"""Trace 評測框架：規則零成本判據 + 可選 LLM-as-Judge。

設計原則
--------
1. **規則判據零成本、零依賴**：引用完整性、來源多樣性、判定留痕三項全部
   由資料結構檢查，不呼叫模型、不產生新數字。這是可對外引用的可信基線。
2. **LLM-as-Judge 可選且降級**：即使 Judge 不可用、逾時或回傳格式錯誤，
   規則判據照樣計算；Judge 失敗絶不影響規則結論（只記為 unavailable）。
3. **可重現**：同一批 trace 必得同一規則結果；Judge 結果單獨標註模型與
   時間，與規則結果分開呈現，避免把主觀評分混進客觀判據。

一條 trace 代表一次 Agent 判定及其依據；可由 explain.EvidenceCard 或
decision_journal 的記錄轉換而來。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from .explain import EvidenceCard

# 規則判據的門檻：來源多樣性要求至少兩個不同來源才算多渠道佐證。
_MIN_DISTINCT_SOURCES = 2


@dataclass(frozen=True)
class RuleResult:
    """單條規則判據的結果。"""

    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class JudgeResult:
    """LLM-as-Judge 結果；failed 時 verdict 為 None，不影響規則判據。"""

    available: bool
    verdict: str | None = None        # pass / fail
    reason: str = ""
    model: str = "unknown"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TraceVerdict:
    """單條 trace 的整體評測：規則判據為準，Judge 僅附加。"""

    trace_id: str
    rules: tuple[RuleResult, ...]
    judge: JudgeResult
    rule_pass: bool                   # 全部規則判據通過

    @property
    def passed(self) -> bool:
        """對外引用只看規則判據；Judge 不參與，確保可信基線穩定。"""
        return self.rule_pass

    def as_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "rules": [rule.as_dict() for rule in self.rules],
            "judge": self.judge.as_dict(),
            "rule_pass": self.rule_pass,
            "passed": self.passed,
        }


def _source_key(source: str, locator: str) -> str:
    return f"{source.strip().casefold()}::{locator.strip().casefold()}"


def rule_checks(trace_id: str, card: EvidenceCard, *,
                known_sources: set[str] | None = None) -> list[RuleResult]:
    """三項零成本規則判據：引用完整性、來源多樣性、判定留痕。

    - 引用完整性：每條 basis 都要有來源；若提供 known_sources，來源須在白名單內。
    - 來源多樣性：CRITICAL/ERROR 級判定須至少兩個不同來源，降低單一來源誤判。
    - 判定留痕：卡片須有 claim、 rationale 與排序，且 traceable 為真。
    """
    results: list[RuleResult] = []
    basis = list(card.basis)

    # 一、引用完整性
    empty = [index for index, item in enumerate(basis) if not item.source.strip()]
    unknown = []
    if known_sources is not None:
        unknown = [item.source for item in basis if item.source not in known_sources]
    if empty or unknown:
        detail_parts = []
        if empty:
            detail_parts.append(f"第 {'、'.join(str(i + 1) for i in empty)} 條依據缺來源")
        if unknown:
            detail_parts.append("來源不在已知清單：" + "、".join(sorted(set(unknown))[:5]))
        results.append(RuleResult("引用完整性", False, "；".join(detail_parts)))
    else:
        results.append(RuleResult("引用完整性", True, f"全部 {len(basis)} 條依據均有有效來源"))

    # 二、來源多樣性：嚴重重判定要求多渠道；輕微判定至少一條依據即可。
    distinct = {_source_key(item.source, item.locator) for item in basis if item.source.strip()}
    need = _MIN_DISTINCT_SOURCES if card.severity in {"critical", "error"} else 1
    if len(distinct) < need:
        results.append(RuleResult(
            "來源多樣性", False,
            f"{card.severity} 級判定需至少 {need} 個不同來源，實際 {len(distinct)} 個"))
    else:
        results.append(RuleResult("來源多樣性", True, f"不同來源 {len(distinct)} 個"))

    # 三、判定留痕：須有判定原文、理由、排序位置，且卡片自述可追溯。
    missing_fields = []
    if not card.claim.strip():
        missing_fields.append("判定原文")
    if not card.rationale.strip():
        missing_fields.append("規則理由")
    if card.rank < 1:
        missing_fields.append("排序位置")
    if not card.traceable:
        missing_fields.append("原始出處或推斷説明")
    if missing_fields:
        results.append(RuleResult("判定留痕", False, "缺少：" + "、".join(missing_fields)))
    else:
        results.append(RuleResult("判定留痕", True, "判定、理由、排序與出處齊全"))

    return results


def judge_trace(card: EvidenceCard, *, llm: Any = None,
                model: str = "unknown") -> JudgeResult:
    """可選 LLM-as-Judge：把依據卡交給模型判定是否一致。

    任何異常（無模型、呼叫失敗、輸出非預期）都回 unavailable，
    絶不拋出異常，也絶不改寫規則判據——這是「LLM 判失敗不影響規則」的落實。
    """
    if llm is None:
        return JudgeResult(available=False, reason="未提供 Judge 模型", model=model)
    prompt = (
        "你是評測員。只回答 pass 或 fail：以下判定理由是否與原始出處一致？\n"
        f"判定：{card.claim}\n理由：{card.rationale}\n"
        "出處：" + "；".join(item.quote or item.source for item in card.basis[:3])
    )
    try:
        raw = llm.complete(prompt) if hasattr(llm, "complete") else llm(prompt)
        text = str(raw).strip().casefold()
        if text.startswith("pass"):
            return JudgeResult(True, "pass", "", model)
        if text.startswith("fail"):
            return JudgeResult(True, "fail", "", model)
        return JudgeResult(False, reason="Judge 輸出非 pass/fail", model=model)
    except Exception as exc:  # Judge 為附加訊號，任何失敗都降級
        return JudgeResult(False, reason=f"Judge 不可用：{type(exc).__name__}", model=model)


def evaluate_trace(trace_id: str, card: EvidenceCard, *, llm: Any = None,
                   known_sources: set[str] | None = None,
                   model: str = "unknown") -> TraceVerdict:
    """評測單條 trace：先算規則判據，再附加可選 Judge。"""
    rules = tuple(rule_checks(trace_id, card, known_sources=known_sources))
    judge = judge_trace(card, llm=llm, model=model) if llm is not None \
        else JudgeResult(False, reason="未啟用 LLM-as-Judge", model=model)
    return TraceVerdict(trace_id, rules, judge, rule_pass=all(r.passed for r in rules))


@dataclass(frozen=True)
class EvalSummary:
    """批次評測摘要：規則通過率為主，Judge 通過率僅供參考。"""

    total: int
    rule_passed: int
    judge_available: int
    judge_passed: int
    failed_rules: tuple[str, ...] = ()

    @property
    def rule_pass_rate(self) -> float:
        return self.rule_passed / self.total if self.total else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "rule_passed": self.rule_passed,
            "rule_pass_rate": round(self.rule_pass_rate, 4),
            "judge_available": self.judge_available,
            "judge_passed": self.judge_passed,
            "failed_rules": list(self.failed_rules),
        }


def evaluate_batch(cards: dict[str, EvidenceCard], *, llm: Any = None,
                   known_sources: set[str] | None = None,
                   model: str = "unknown") -> EvalSummary:
    """批次評測；回傳可放進基準報告的結構化摘要。"""
    verdicts = [
        evaluate_trace(trace_id, card, llm=llm, known_sources=known_sources, model=model)
        for trace_id, card in cards.items()
    ]
    failed = sorted({
        rule.name for verdict in verdicts for rule in verdict.rules if not rule.passed
    })
    return EvalSummary(
        total=len(verdicts),
        rule_passed=sum(1 for verdict in verdicts if verdict.rule_pass),
        judge_available=sum(1 for verdict in verdicts if verdict.judge.available),
        judge_passed=sum(1 for verdict in verdicts if verdict.judge.verdict == "pass"),
        failed_rules=tuple(failed),
    )


def eval_markdown(summary: EvalSummary) -> str:
    """把評測摘要渲染為 Markdown，明確區分規則判據與 Judge。"""
    return "\n".join([
        "# Trace 評測摘要",
        "",
        f"- 規則判據通過：{summary.rule_passed}/{summary.total}（{summary.rule_pass_rate:.0%}）",
        f"- LLM-as-Judge 可用：{summary.judge_available}；判定通過：{summary.judge_passed}",
        "- 對外口徑：以上通過率以**規則判據**為準；Judge 僅為附加訊號，"
        "其失敗或不可用不影響規則結果。",
    ]) + "\n"
