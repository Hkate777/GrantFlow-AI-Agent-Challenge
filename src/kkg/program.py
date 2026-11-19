"""计划配置：每个资助计划的要求用 YAML 描述，规则引擎按配置执行。

这对应场景 16 的核心难点：不同计划的申请要求、格式、资格、评审准则各不相同，
因此把「计划规则」做成声明式配置，而非写死在代码里。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class RequiredDoc:
    name: str                       # 文件名关键词匹配（不区分大小写）
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
    excluded_keywords: list[str] = field(default_factory=list)   # 项目标题/描述中出现即不合格


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
    divergence_threshold: float = 15.0   # 评分差异阈值（百分制），超过即需讨论
    conflict_rules: dict[str, list[str]] = field(default_factory=dict)  # reviewer -> 冲突关键词

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
