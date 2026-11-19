"""统一数据模型：申请、文档、检查结果、评审、追踪记录。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
import json
import uuid


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
    MISSING = "missing"          # 缺失资料
    DUPLICATE = "duplicate"      # 重复提交
    FORMAT = "format"            # 格式问题
    ELIGIBILITY = "eligibility"  # 资格不符
    CONFLICT = "conflict"        # 潜在利益冲突
    ANOMALY = "anomaly"          # 数值/内容异常
    INCONSISTENT = "inconsistent"  # 跨源数据矛盾


@dataclass
class Document:
    path: str
    name: str
    doc_type: DocType
    text: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class Application:
    app_id: str
    program: str
    title: str
    applicant: str
    email: str = ""
    team: list[str] = field(default_factory=list)
    fields: dict[str, Any] = field(default_factory=dict)   # 结构化表单字段
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
    source: str = ""          # 触发来源（规则名/文档名）
    needs_human: bool = False  # 是否需人工复核


@dataclass
class ReviewScore:
    reviewer: str
    criteria: dict[str, float]  # 维度 -> 分数
    comment: str = ""

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
