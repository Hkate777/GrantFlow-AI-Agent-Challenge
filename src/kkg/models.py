"""統一數據模型：申請、文檔、檢查結果、評審、追蹤記錄。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
import json
import unicodedata
import uuid


def identity_key(value: str) -> str:
    """結構化身份鍵：統一字形、空白與大小寫；舊資料的姓名亦可安全回退。"""
    return unicodedata.normalize("NFKC", value or "").strip().casefold()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class DocType(str, Enum):
    TEXT = "text"
    MARKDOWN = "markdown"
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"
    CSV = "csv"
    IMAGE = "image"
    VIDEO = "video"
    OTHER = "other"


class FindingType(str, Enum):
    MISSING = "missing"          # 缺失資料
    DUPLICATE = "duplicate"      # 重複提交
    FORMAT = "format"            # 格式問題
    ELIGIBILITY = "eligibility"  # 資格不符
    CONFLICT = "conflict"        # 潛在利益衝突
    ANOMALY = "anomaly"          # 數值/內容異常
    INCONSISTENT = "inconsistent"  # 跨源數據矛盾
    SECURITY = "security"        # 安全/提示注入風險（需人工覆核）


@dataclass
class EvidenceSpan:
    locator: str
    text: str


@dataclass
class Document:
    path: str
    name: str
    doc_type: DocType
    text: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    segments: list[EvidenceSpan] = field(default_factory=list)  # 原文證據位置（頁碼/行/表格）


@dataclass
class Application:
    app_id: str
    program: str
    title: str
    applicant: str
    email: str = ""
    team: list[str] = field(default_factory=list)
    fields: dict[str, Any] = field(default_factory=dict)   # 結構化表單字段
    docs: list[Document] = field(default_factory=list)

    @staticmethod
    def new_id() -> str:
        return f"APP-{uuid.uuid4().hex[:8].upper()}"


@dataclass
class Finding:
    finding_type: FindingType
    severity: Severity
    message: str
    detail: str = ""
    source: str = ""          # 觸發來源（規則名/文檔名）
    needs_human: bool = False  # 是否需人工複核
    application_ids: list[str] = field(default_factory=list)  # 批次發現涉及的申請
    # 結構化主體標識（例如利益衝突所屬的評審帳號 ID）；舊記錄留空時兼容姓名比對。
    subject_id: str = ""


@dataclass
class ReviewScore:
    reviewer: str
    criteria: dict[str, float]  # 維度 -> 分數
    comment: str = ""
    # 評審員的結構化身份：Web 工作台由服務端登入帳號綁定，舊資料回退至姓名正規化。
    reviewer_id: str = ""

    @property
    def total(self) -> float:
        return sum(self.criteria.values()) if self.criteria else 0.0


@dataclass
class Milestone:
    name: str
    due: str                 # ISO date
    status: str = "pending"  # pending / done / overdue
    funding_release: float = 0.0


@dataclass
class AuditEvent:
    ts: str
    agent: str
    action: str
    detail: str = ""


@dataclass
class AuditTrail:
    events: list[AuditEvent] = field(default_factory=list)

    def log(self, agent: str, action: str, detail: str = "") -> None:
        self.events.append(AuditEvent(ts=_now(), agent=agent, action=action, detail=detail))

    def to_json(self) -> str:
        return json.dumps([asdict(e) for e in self.events], ensure_ascii=False, indent=2)
