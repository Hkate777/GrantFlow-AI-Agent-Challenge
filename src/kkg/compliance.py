"""合規三級分級：refuse / degrade / note，取代非黑即白的衝突判定。

為何分級
--------
真實合規語義不是「命中就淘汰」：明確禁止項（賭博、武器）才應該拒絕；
潛在利益衝突、預算超上限應降級為人工覆核；法規框架提示只是提醒。
原有 conflict 判定一律 CRITICAL，既嚇人又缺乏法規依據，難以對外解釋。

三級定義
--------
- refuse  ：明確禁止，直接不通過（需人工簽發確認）。
- degrade ：需要人工覆核／迴避／補件，降級處理而非直接淘汰。
- note    ：建議性提示，不影響資格判定，只提醒對照法規框架。

能力邊界（必須隨報告對外呈現）
-----------------------------
「未命中不代表合規，只表示本層未覆蓋。」規則只能偵測已明文列出的模式，
覆蓋不到之處不等於沒有風險；這個聲明用來治「虛假安全感」，絶不可省略。
"""
from __future__ import annotations

import math
import unicodedata
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from .models import Application, Finding, FindingType, Severity, identity_key
from .program import Program

COVERAGE_STATEMENT = "未命中不代表合規，只表示本層規則未覆蓋；人工覆核仍不可省略。"


class ComplianceLevel(str, Enum):
    REFUSE = "refuse"
    DEGRADE = "degrade"
    NOTE = "note"


# 層級 -> 呈現順序與對應 Finding 嚴重度
_LEVEL_ORDER = {ComplianceLevel.REFUSE: 0, ComplianceLevel.DEGRADE: 1, ComplianceLevel.NOTE: 2}
_LEVEL_SEVERITY = {
    ComplianceLevel.REFUSE: Severity.CRITICAL,
    ComplianceLevel.DEGRADE: Severity.WARNING,
    ComplianceLevel.NOTE: Severity.INFO,
}
_LEVEL_LABEL = {
    ComplianceLevel.REFUSE: "拒絕",
    ComplianceLevel.DEGRADE: "降級覆核",
    ComplianceLevel.NOTE: "提示",
}

# 未提供法規依據時的預設説明（明確標示為「計劃規則」而非法律條文，避免冒充法規）
_DEFAULT_BASIS = {
    ComplianceLevel.REFUSE: "計劃資格規則：明文列為禁止的項目類別。",
    ComplianceLevel.DEGRADE: "計劃評審規則：可能影響公正性或資源上限，須人工覆核。",
    ComplianceLevel.NOTE: "計劃合規提示：建議對照適用法規框架，非硬性資格條件。",
}


@dataclass(frozen=True)
class ComplianceHit:
    """單條合規命中：層級、規則名、法規依據、原文摘錄與來源。"""

    level: ComplianceLevel
    rule: str
    basis: str                      # 法規／規則依據（可追溯）
    excerpt: str = ""               # 已去識別化的短摘錄
    source: str = ""                # 觸發位置
    subject_id: str = ""            # 結構化主體（如利益衝突評審帳號）
    application_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["level"] = self.level.value
        data["label"] = _LEVEL_LABEL[self.level]
        data["application_ids"] = list(self.application_ids)
        return data

    @property
    def narrative(self) -> str:
        where = f"（{self.source}）" if self.source else ""
        return f"[{_LEVEL_LABEL[self.level]}] {self.rule}{where}：依據 {self.basis}"


@dataclass(frozen=True)
class ComplianceReport:
    """合規分級報告：按層級分組，並強制附帶能力邊界聲明。"""

    app_id: str
    hits: tuple[ComplianceHit, ...]
    coverage_statement: str = COVERAGE_STATEMENT

    def by_level(self, level: ComplianceLevel) -> tuple[ComplianceHit, ...]:
        return tuple(hit for hit in self.hits if hit.level == level)

    @property
    def refuse(self) -> bool:
        """只有 refuse 級命中才建議拒絕（仍需人工簽發確認）。"""
        return any(hit.level == ComplianceLevel.REFUSE for hit in self.hits)

    @property
    def needs_human(self) -> bool:
        return any(hit.level in {ComplianceLevel.REFUSE, ComplianceLevel.DEGRADE}
                   for hit in self.hits)

    def as_dict(self) -> dict[str, Any]:
        return {
            "app_id": self.app_id,
            "refuse": self.refuse,
            "needs_human": self.needs_human,
            "hits": [hit.as_dict() for hit in self.hits],
            "coverage_statement": self.coverage_statement,
        }

    def markdown(self) -> str:
        """渲染為可供人工覆核的 Markdown，末尾必定附上能力邊界聲明。"""
        lines = [f"# 合規分級報告 · {self.app_id}", ""]
        if not self.hits:
            lines.append("本層未命中任何規則。")
        for level in (ComplianceLevel.REFUSE, ComplianceLevel.DEGRADE, ComplianceLevel.NOTE):
            group = self.by_level(level)
            if not group:
                continue
            lines.append(f"## {_LEVEL_LABEL[level]}（{level.value}）")
            for hit in group:
                lines.append(f"- {hit.rule}：{hit.basis}")
                if hit.excerpt:
                    lines.append(f"  - 摘錄：{hit.excerpt}")
            lines.append("")
        lines.append(f"> {self.coverage_statement}")
        return "\n".join(lines) + "\n"


def _text(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").strip().casefold()


def _contains(text: str, keyword: str) -> bool:
    key = _text(keyword)
    if not key:
        return False
    if key.isascii():
        import re
        return re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", text) is not None
    return key in text


def classify(app: Application, program: Program, *,
             legal_basis: dict[str, str] | None = None) -> ComplianceReport:
    """對申請做三級合規分級；每條命中附法規依據，未命中也回能力邊界聲明。

    ``legal_basis`` 可為特定規則提供法規條文依據（鍵為規則名）；未提供時
    標示為「計劃規則」而非冒充法律條文。
    """
    basis_map = legal_basis or {}
    hits: list[ComplianceHit] = []

    def basis_for(rule: str, level: ComplianceLevel) -> str:
        return basis_map.get(rule) or _DEFAULT_BASIS[level]

    # 收集可搜尋的原始文本與位置
    sources: list[tuple[str, str]] = [
        ("application.title", app.title),
        ("application.applicant", app.applicant),
        *(("team", member) for member in app.team),
        *(("fields." + key, str(value)) for key, value in app.fields.items()),
    ]
    for doc in app.docs:
        if doc.segments:
            sources.extend((f"{doc.name} · {seg.locator}", seg.text) for seg in doc.segments)
        else:
            sources.append((doc.name, doc.text))
    normalized = [(source, _text(value), value) for source, value in sources]

    # 一、明確禁止項 → refuse
    excluded = getattr(program.eligibility, "excluded_keywords", []) or []
    for keyword in excluded:
        for source, lowered, raw in normalized:
            if _contains(lowered, keyword):
                hits.append(ComplianceHit(
                    ComplianceLevel.REFUSE, f"禁止類別：{keyword}",
                    basis_for(keyword, ComplianceLevel.REFUSE),
                    excerpt=_excerpt(raw), source=source,
                    application_ids=(app.app_id,)))
                break

    # 二、潛在利益衝突 → degrade（不再是單純 CRITICAL）
    for reviewer, keywords in (program.conflict_rules or {}).items():
        for keyword in keywords:
            for source, lowered, raw in normalized:
                if _contains(lowered, keyword):
                    hits.append(ComplianceHit(
                        ComplianceLevel.DEGRADE, f"利益衝突：{reviewer}",
                        basis_for("利益衝突", ComplianceLevel.DEGRADE),
                        excerpt=_excerpt(f"{keyword} 出現於 {source}"), source=source,
                        subject_id=identity_key(reviewer),
                        application_ids=(app.app_id,)))
                    break

    # 三、預算超上限 → degrade
    budget = _to_number(app.fields.get("budget")) or _to_number(app.fields.get("requested_budget"))
    if budget is not None and program.budget_cap is not None and budget > program.budget_cap:
        hits.append(ComplianceHit(
            ComplianceLevel.DEGRADE, "申請預算超出計劃上限",
            basis_for("預算上限", ComplianceLevel.DEGRADE),
            source="fields.budget", application_ids=(app.app_id,)))

    # 四、法規框架提示 → note（未提及才提示，且明確為建議性）
    legal = getattr(app, "legal_context", "") or ""
    if legal.strip() and not any(_contains(_text(legal), kw) for kw in ("法", "規", "act", "law", "privacy")):
        hits.append(ComplianceHit(
            ComplianceLevel.NOTE, "建議説明適用合規框架",
            basis_for("合規框架", ComplianceLevel.NOTE),
            source="application.legal_context", application_ids=(app.app_id,)))

    hits.sort(key=lambda hit: (_LEVEL_ORDER[hit.level], hit.rule))
    return ComplianceReport(app.app_id, tuple(hits), COVERAGE_STATEMENT)


def to_findings(report: ComplianceReport) -> list[Finding]:
    """把合規分級轉為現有 Finding 管線可消費的格式。"""
    result: list[Finding] = []
    for hit in report.hits:
        result.append(Finding(
            finding_type=(FindingType.ELIGIBILITY if hit.level == ComplianceLevel.REFUSE
                          else FindingType.CONFLICT if hit.rule.startswith("利益衝突")
                          else FindingType.ANOMALY if hit.level == ComplianceLevel.DEGRADE
                          else FindingType.ELIGIBILITY),
            severity=_LEVEL_SEVERITY[hit.level],
            message=hit.narrative,
            detail=f"法規／規則依據：{hit.basis}",
            source=f"compliance.{hit.level.value}",
            needs_human=hit.level in {ComplianceLevel.REFUSE, ComplianceLevel.DEGRADE},
            application_ids=list(hit.application_ids),
            subject_id=hit.subject_id,
        ))
    return result


def _excerpt(text: str, limit: int = 60) -> str:
    compact = " ".join((text or "").split())
    return compact[:limit] + ("…" if len(compact) > limit else "")


def _to_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "").replace("HK$", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
