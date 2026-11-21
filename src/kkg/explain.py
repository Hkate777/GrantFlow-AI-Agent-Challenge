"""確定性依據卡：每個判定都回答「基於哪條原始數據、排第幾、為什麼」。

設計原則
--------
1. **零 LLM**：話術完全由規則從原始數據與分數生成，不呼叫任何模型；
   同一輸入必得同一輸出，可重現、可稽核。
2. **零新數字**：依據卡正文不引入輸入以外的數字。判定原文（claim）如含
   金額/分數，全部來自原始數據；排序以中文序數呈現，不另造阿拉伯數字。
   若需機械核對，可把卡片文字交給 guard.check_ledger() 驗證。
3. **missing 明確標記**：缺失值走 imputed 標記並寫明推斷來源，絶不靜默填值。
   這讓人工覆核一眼看出「哪個數字是推斷的」，避免黑盒。

這對應合規審查最重視的「可追溯」：Finding 不再只是結論，而是附帶
原始出處、排序位置與規則理由的依據卡。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .models import Finding, FindingType, Severity

# 由 finding 類型映射到固定理由模板——全部為文字，不含任何數字。
_RATIONALE: dict[FindingType, str] = {
    FindingType.MISSING: "必要欄位或附件在原始申請中未出現，按計劃必須項規則標記。",
    FindingType.DUPLICATE: "與其他申請在識別欄位上出現相同值，按查重規則標記為疑似重複。",
    FindingType.FORMAT: "原始內容的格式或字形與地區標準不符，按格式規則標記。",
    FindingType.ELIGIBILITY: "原始內容未能對應計劃的資格條件，按資格規則標記為建議核對。",
    FindingType.CONFLICT: "原始內容出現與評審員相關的關鍵詞，按利益衝突規則標記為建議迴避。",
    FindingType.ANOMALY: "原始數值偏離同批資料的合理範圍，按異常檢測規則標記。",
    FindingType.INCONSISTENT: "不同來源對同一事項給出不一致的值，按交叉驗證規則標記。",
    FindingType.SECURITY: "原始內容出現可疑指令特徵，按安全護欄規則標記為人工覆核。",
}

# 中文序數：避免把排序位置寫成阿拉伯數字而混淆原始數據數字。
_ORDINAL = ("第一", "第二", "第三", "第四", "第五", "第六", "第七", "第八",
            "第九", "第十", "第十一", "第十二", "第十三", "第十四", "第十五")


def ordinal_label(rank: int) -> str:
    """回傳中文序數；超出內建表時以「第N」形式補充（N 為排序位置，非數據）。"""
    if 1 <= rank <= len(_ORDINAL):
        return _ORDINAL[rank - 1]
    return f"第{rank}"


@dataclass(frozen=True)
class EvidenceBasis:
    """一條原始出處：來源、位置、原文摘錄、結構化值。"""

    source: str
    locator: str = ""
    quote: str = ""
    value: str = ""

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceCard:
    """單條判定的可追溯依據卡。"""

    claim: str                      # 判定原文（來自 finding.message，零改寫）
    finding_type: str               # 判定類型
    severity: str                   # 嚴重度
    rank: int                       # 在全部判定中的排序位置（由 1 起）
    rank_label: str                 # 中文序數（供話術使用，不引入阿拉伯數字）
    rationale: str                  # 為什麼：規則理由（固定模板，零新數字）
    basis: tuple[EvidenceBasis, ...] = ()  # 基於哪條原始數據
    imputed: bool = False           # 是否含推斷值
    imputed_note: str = ""          # 推斷説明（imputed 時必填）
    application_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "finding_type": self.finding_type,
            "severity": self.severity,
            "rank": self.rank,
            "rank_label": self.rank_label,
            "rationale": self.rationale,
            "basis": [item.as_dict() for item in self.basis],
            "imputed": self.imputed,
            "imputed_note": self.imputed_note,
            "application_ids": list(self.application_ids),
            "narrative": self.narrative,
            "traceable": self.traceable,
        }

    @property
    def traceable(self) -> bool:
        """有原始出處才算是可追溯；推斷值須另有 imputed 説明。"""
        return bool(self.basis) and (not self.imputed or bool(self.imputed_note))

    @property
    def narrative(self) -> str:
        """一句話話術：排序 + 判定 + 理由 + 出處，全部沿用輸入原有數字。"""
        where = "、".join(
            "／".join(part for part in (item.source, item.locator) if part)
            for item in self.basis[:3]
        )
        tail = f"（出處：{where}）" if where else "（原始出處待補）"
        if self.imputed:
            tail += f"（含推斷值：{self.imputed_note}）"
        return f"{self.rank_label}判定：{self.claim}。理由：{self.rationale}{tail}"


def _default_basis(finding: Finding) -> tuple[EvidenceBasis, ...]:
    """沒有外部佐證時，至少保留 finding 自身的來源與細節作為線索。"""
    if not finding.source and not finding.detail:
        return ()
    return (EvidenceBasis(source=finding.source or "規則引擎", quote=finding.detail),)


def evidence_for(finding: Finding, *, rank: int = 1,
                 basis: list[EvidenceBasis] | None = None,
                 imputed: bool = False, imputed_note: str = "") -> EvidenceCard:
    """由單條 Finding 生成依據卡；完全規則式，零 LLM、零新數字。"""
    if rank < 1:
        raise ValueError("排序位置須從 1 開始。")
    if imputed and not imputed_note.strip():
        raise ValueError("標記為推斷值時必須提供 imputed_note，不得靜默填值。")
    chosen = tuple(basis) if basis else _default_basis(finding)
    rationale = _RATIONALE.get(finding.finding_type, "按規則引擎判定，具體理由待人工覆核。")
    return EvidenceCard(
        claim=finding.message,
        finding_type=finding.finding_type.value,
        severity=finding.severity.value,
        rank=rank,
        rank_label=ordinal_label(rank),
        rationale=rationale,
        basis=chosen,
        imputed=imputed,
        imputed_note=imputed_note.strip(),
        application_ids=tuple(finding.application_ids),
    )


# 排序權重：越嚴重的判定排越前，作為「排第幾」的穩定依據。
_SEVERITY_ORDER = {
    Severity.CRITICAL: 0,
    Severity.ERROR: 1,
    Severity.WARNING: 2,
    Severity.INFO: 3,
}


def evidence_bundle(findings: list[Finding], *,
                    basis_by_source: dict[str, list[EvidenceBasis]] | None = None
                    ) -> list[EvidenceCard]:
    """批量生成依據卡，按嚴重度穩定排序並給出排序位置。

    ``basis_by_source`` 可把 finding.source 對應到原始佐證（例如由 evidence.py
    擷取的段落）；沒有對應時退回 finding 自身的來源線索。
    """
    ordered = sorted(
        enumerate(findings),
        key=lambda pair: (_SEVERITY_ORDER.get(pair[1].severity, 4), pair[0]),
    )
    cards: list[EvidenceCard] = []
    for rank, (_, finding) in enumerate(ordered, 1):
        basis = (basis_by_source or {}).get(finding.source)
        cards.append(evidence_for(finding, rank=rank, basis=basis))
    return cards


def cards_markdown(cards: list[EvidenceCard], *, title: str = "判定依據卡") -> str:
    """把依據卡渲染為 Markdown；適合放入評審包供人工逐條覆核。"""
    lines = [f"# {title}", ""]
    if not cards:
        lines.append("沒有需要覆核的判定。")
        return "\n".join(lines) + "\n"
    for card in cards:
        lines.append(f"## {card.rank_label}：{card.claim}")
        lines.append(f"- 類型：{card.finding_type}／嚴重度：{card.severity}")
        lines.append(f"- 理由：{card.rationale}")
        if card.basis:
            for item in card.basis:
                where = "／".join(part for part in (item.source, item.locator) if part)
                quote = f"「{item.quote}」" if item.quote else ""
                lines.append(f"- 原始出處：{where}{quote}")
        else:
            lines.append("- 原始出處：待補（此卡不可視為已追溯）")
        if card.imputed:
            lines.append(f"- 推斷值：{card.imputed_note}")
        lines.append("")
    return "\n".join(lines)
