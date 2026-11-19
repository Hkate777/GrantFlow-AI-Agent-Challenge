"""計劃配置：每個資助計劃的要求用 YAML 描述，規則引擎按配置執行。

這對應場景 16 的核心難點：不同計劃的申請要求、格式、資格、評審準則各不相同，
因此把「計劃規則」做成聲明式配置，而非寫死在代碼裏。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class RequiredDoc:
    name: str                       # 文件名關鍵詞匹配（不區分大小寫）
    doc_type: str = "any"           # text/pdf/docx/xlsx/image/any
    required: bool = True


@dataclass
class RequiredField:
    key: str
    label: str = ""
    ftype: str = "str"              # str / number / email / list
    min_value: float | None = None
    max_value: float | None = None


@dataclass
class ReviewCriterion:
    name: str
    weight: float = 1.0
    max_score: float = 25.0


@dataclass
class Eligibility:
    min_team: int = 1
    max_team: int = 10
    allowed_org_types: list[str] = field(default_factory=list)  # 空 = 不限制
    excluded_keywords: list[str] = field(default_factory=list)   # 項目標題/描述中出現即不合格


@dataclass
class Program:
    program_id: str
    name: str
    deadline: str = ""
    required_docs: list[RequiredDoc] = field(default_factory=list)
    required_fields: list[RequiredField] = field(default_factory=list)
    criteria: list[ReviewCriterion] = field(default_factory=list)
    eligibility: Eligibility = field(default_factory=Eligibility)
    budget_cap: float | None = None
    divergence_threshold: float = 15.0   # 評分差異閾值（百分制），超過即需討論
    conflict_rules: dict[str, list[str]] = field(default_factory=dict)  # reviewer -> 衝突關鍵詞

    @staticmethod
    def load(path: str | Path) -> "Program":
        raw: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        elig = raw.get("eligibility", {})
        return Program(
            program_id=raw["program_id"],
            name=raw["name"],
            deadline=raw.get("deadline", ""),
            required_docs=[RequiredDoc(**d) for d in raw.get("required_docs", [])],
            required_fields=[RequiredField(**f) for f in raw.get("required_fields", [])],
            criteria=[ReviewCriterion(**c) for c in raw.get("criteria", [])],
            eligibility=Eligibility(**elig),
            budget_cap=raw.get("budget_cap"),
            divergence_threshold=float(raw.get("divergence_threshold", 15.0)),
            conflict_rules=raw.get("conflict_rules", {}),
        )
