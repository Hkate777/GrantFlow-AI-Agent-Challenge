"""決策日誌與回看驗證：把審計從「誰何時改了狀態」升級為質量閉環。

既有 AuditTrail 只記操作痕跡，無法回答「這個 Agent 的判定到底準不準」。
本模組結構化記錄每次 LLM 判定（模型／置信度／依據／風險），支援：

1. 事後 N 天回看對帳：找出已過回看期、仍未人工覆核的判定。
2. 人工結論回填：agree / disagree，附覆核備註。
3. 按模型拆分準確率：只統計已覆核的判定，未覆核不計入分母。

日期以 ``today`` 參數注入，避免測試受執行當日時區影響；絶不硬編碼固定日期。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Decision:
    """一條結構化 LLM 判定記錄。"""

    decision_id: str
    app_id: str
    decision: str                   # 判定結論（如 approved / needs_review）
    model: str                      # 模型識別（如 mock / gpt-4o-mini）
    confidence: float | None        # 模型自報置信度；無則 None，不偷偷填值
    rationale: str = ""             # 判定理由（對應依據卡 rationale）
    basis: tuple[str, ...] = ()     # 原始出處識別（來源／位置）
    risks: tuple[str, ...] = ()     # 風險標記
    created_at: str = ""            # ISO 時間戳
    reviewed_at: str | None = None  # 人工覆核時間
    verdict: str | None = None      # agree / disagree
    review_note: str = ""

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["basis"] = list(self.basis)
        data["risks"] = list(self.risks)
        return data

    @property
    def due_date(self) -> date:
        """由建立時間推算的日期；無時間戳時回傳今日（向後相容舊記錄）。"""
        if not self.created_at:
            return date.today()
        try:
            return datetime.fromisoformat(self.created_at).date()
        except ValueError:
            return date.today()


@dataclass(frozen=True)
class ModelAccuracy:
    """單一模型的準確率；未覆核判定不進分母。"""

    model: str
    reviewed: int
    agreed: int
    accuracy: float | None          # reviewed 為零時 None，不當成零分

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewQueueItem:
    """待回看項目：判定與已逾期天數。"""

    decision: Decision
    overdue_days: int

    def as_dict(self) -> dict[str, Any]:
        return {"decision": self.decision.as_dict(), "overdue_days": self.overdue_days}


class DecisionJournal:
    """SQLite 支撐的決策日誌；與個案儲存分離，避免狀態改寫遺失判定痕跡。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def _connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("""CREATE TABLE IF NOT EXISTS decisions (
                decision_id TEXT PRIMARY KEY,
                app_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                model TEXT NOT NULL,
                confidence REAL,
                rationale TEXT NOT NULL DEFAULT '',
                basis TEXT NOT NULL DEFAULT '[]',
                risks TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                verdict TEXT,
                review_note TEXT NOT NULL DEFAULT ''
            )""")
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _row_to_decision(row: sqlite3.Row) -> Decision:
        return Decision(
            decision_id=row["decision_id"], app_id=row["app_id"],
            decision=row["decision"], model=row["model"],
            confidence=row["confidence"], rationale=row["rationale"],
            basis=tuple(json.loads(row["basis"])), risks=tuple(json.loads(row["risks"])),
            created_at=row["created_at"], reviewed_at=row["reviewed_at"],
            verdict=row["verdict"], review_note=row["review_note"],
        )

    def record(self, app_id: str, decision: str, *, model: str = "unknown",
               confidence: float | None = None, rationale: str = "",
               basis: list[str] | tuple[str, ...] = (),
               risks: list[str] | tuple[str, ...] = (),
               decision_id: str = "", created_at: str = "") -> Decision:
        """記錄一條判定；ID 與時間缺省時自動產生，方便測試注入固定值。"""
        if confidence is not None and (type(confidence) not in {int, float}
                                       or not 0.0 <= float(confidence) <= 1.0):
            raise ValueError("confidence 必須介於 0 與 1 之間。")
        entry = Decision(
            decision_id=decision_id or f"D-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
            app_id=app_id, decision=decision, model=model,
            confidence=float(confidence) if confidence is not None else None,
            rationale=rationale, basis=tuple(basis), risks=tuple(risks),
            created_at=created_at or _utc_now(),
        )
        with self._connect(write=True) as connection:
            connection.execute("""INSERT INTO decisions
                (decision_id, app_id, decision, model, confidence, rationale, basis,
                 risks, created_at, reviewed_at, verdict, review_note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                entry.decision_id, entry.app_id, entry.decision, entry.model,
                entry.confidence, entry.rationale, json.dumps(list(entry.basis), ensure_ascii=False),
                json.dumps(list(entry.risks), ensure_ascii=False), entry.created_at,
                None, None, "",
            ))
        return entry

    def get(self, decision_id: str) -> Decision | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM decisions WHERE decision_id = ?", (decision_id,)).fetchone()
        return self._row_to_decision(row) if row else None

    def list(self, *, model: str | None = None, limit: int | None = None) -> list[Decision]:
        query = "SELECT * FROM decisions"
        params: list[Any] = []
        if model is not None:
            query += " WHERE model = ?"
            params.append(model)
        query += " ORDER BY rowid DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_decision(row) for row in rows]

    def review(self, decision_id: str, verdict: str, *, note: str = "",
               reviewed_at: str = "") -> Decision:
        """回填人工覆核結論；verdict 只接受 agree / disagree。"""
        if verdict not in {"agree", "disagree"}:
            raise ValueError("verdict 必須是 agree 或 disagree。")
        with self._connect(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM decisions WHERE decision_id = ?", (decision_id,)).fetchone()
            if row is None:
                raise KeyError("找不到決策記錄。")
            connection.execute("""UPDATE decisions
                SET reviewed_at = ?, verdict = ?, review_note = ? WHERE decision_id = ?""", (
                reviewed_at or _utc_now(), verdict, note, decision_id,
            ))
            updated = connection.execute(
                "SELECT * FROM decisions WHERE decision_id = ?", (decision_id,)).fetchone()
        return self._row_to_decision(updated)

    def due_for_review(self, days: int = 7, *, today: date | None = None) -> list[ReviewQueueItem]:
        """找出建立超過 N 天、仍未人工覆核的判定，供回看對帳。"""
        if days < 0:
            raise ValueError("days 必須非負。")
        reference = today or date.today()
        result: list[ReviewQueueItem] = []
        for entry in self.list():
            if entry.verdict is not None:
                continue
            overdue = (reference - entry.due_date).days
            if overdue >= days:
                result.append(ReviewQueueItem(entry, overdue))
        return result

    def accuracy_by_model(self) -> list[ModelAccuracy]:
        """按模型拆分準確率；只統計已覆核判定，未覆核不進分母。"""
        grouped: dict[str, dict[str, int]] = {}
        for entry in self.list():
            if entry.verdict is None:
                continue
            stats = grouped.setdefault(entry.model, {"reviewed": 0, "agreed": 0})
            stats["reviewed"] += 1
            if entry.verdict == "agree":
                stats["agreed"] += 1
        return [
            ModelAccuracy(model, stats["reviewed"], stats["agreed"],
                          stats["agreed"] / stats["reviewed"] if stats["reviewed"] else None)
            for model, stats in sorted(grouped.items())
        ]

    def journal_markdown(self, *, days: int = 7, today: date | None = None) -> str:
        """輸出可供答辯展示的品質報告：待回看清單 + 按模型準確率。"""
        due = self.due_for_review(days, today=today)
        accuracy = self.accuracy_by_model()
        lines = [
            "# 決策日誌與品質回看",
            "",
            f"- 待回看判定（超過 {days} 天）：{len(due)} 條",
            f"- 已覆核模型數：{len(accuracy)}",
            "",
            "## 按模型準確率（僅計已覆核）",
            "",
            "| 模型 | 已覆核 | 一致 | 準確率 |",
            "|---|---:|---:|---:|",
        ]
        for stat in accuracy:
            rate = f"{stat.accuracy:.0%}" if stat.accuracy is not None else "—"
            lines.append(f"| {stat.model} | {stat.reviewed} | {stat.agreed} | {rate} |")
        if due:
            lines += ["", "## 待回看", ""]
            for item in due[:10]:
                entry = item.decision
                lines.append(f"- {entry.app_id}／{entry.model}：{entry.decision}"
                             f"（逾期 {item.overdue_days} 天）")
        lines += ["", "> 未覆核的判定不計入準確率；此報告不宣稱 Agent 判定已 Fully 驗證。"]
        return "\n".join(lines) + "\n"
