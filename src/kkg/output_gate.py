"""對外輸出閘門：所有對外發布的評審物料必經此層。

README 所述的「輸出閘門」不是抽象概念：本模組把去識別化與
ensure_safe_output() 串成實際呼叫點，供 Web 評審包、CLI 評審包、
Excel 總表與結果通知共用。即使上游拼接出幻覺敏感值，也會在此處
被佔位符取代；閘門命中只回傳類型與次數，不回傳原始值。
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from .models import Application, Finding
from .privacy import Redactor, ensure_safe_output


def redact_value(value: Any, redactor: Redactor) -> Any:
    """遞迴去識別化字串（dict/list 保持結構）。"""
    if isinstance(value, str):
        return redactor.redact(value)
    if isinstance(value, dict):
        return {key: redact_value(item, redactor) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(item, redactor) for item in value]
    return value


def redact_findings(findings: list[Finding], redactor: Redactor) -> list[Finding]:
    """把 Finding 的所有文字欄位（含結構化主體 ID）去識別化。"""
    return [replace(
        finding,
        message=redactor.redact(finding.message),
        detail=redactor.redact(finding.detail),
        source=redactor.redact(finding.source),
        subject_id=redactor.redact(finding.subject_id),
        application_ids=[redactor.redact(item) for item in finding.application_ids],
    ) for finding in findings]


def gate_text(text: str, redactor: Redactor | None = None,
              ledger: frozenset[str] | set[str] | None = None
              ) -> tuple[str, dict[str, int]]:
    """文字輸出閘門：回傳 (安全文字, 命中統計)。

    傳入 ledger 時併用數字台賬守衛：台賬外的金額/分數/日期視為幻覺並記入
    統計 ``數字幻覺``；閘門只統計與標記，不竄改原文，由人工覆核取捨。
    """
    safe, hits = ensure_safe_output(text or "", redactor or Redactor())
    if ledger is not None:
        from .guard import check_ledger
        verdict = check_ledger(safe, ledger)
        if not verdict.ok:
            hits = {**hits, "數字幻覺": len(verdict.unknown)}
    return safe, hits


def gate_bundle(app: Application, extracted: dict[str, Any], findings: list[Finding],
                *, redactor: Redactor | None = None, notice: str = "") -> dict[str, Any]:
    """建立可安全對外發布的評審資料：申請視圖、抽取結果、發現與通知。"""
    active = redactor or Redactor()
    safe_notice, notice_hits = gate_text(notice, active)
    return {
        "application": active.safe_view(app),
        "extracted": redact_value(extracted, active),
        "findings": redact_findings(findings, active),
        "notice": safe_notice,
        "gate_hits": notice_hits,
        "redactor": active,
    }


def gate_directory(directory: str | Path, redactor: Redactor | None = None) -> dict[str, int]:
    """對已生成的評審包逐檔套用輸出閘門；只重寫有命中的檔案。"""
    active = redactor or Redactor()
    hits: dict[str, int] = {}
    for path in sorted(Path(directory).rglob("*")):
        if not path.is_file() or path.suffix.casefold() not in {".md", ".json", ".txt", ".csv"}:
            continue
        try:
            original = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        safe, found = gate_text(original, active)
        if found:
            path.write_text(safe, encoding="utf-8")
            for kind, count in found.items():
                hits[kind] = hits.get(kind, 0) + count
    return hits
