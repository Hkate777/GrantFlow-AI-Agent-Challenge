"""計劃配置：每個資助計劃的要求用 YAML 描述，規則引擎按配置執行。

這對應場景 16 的核心難點：不同計劃的申請要求、格式、資格、評審準則各不相同，
因此把「計劃規則」做成聲明式配置，而非寫死在代碼裏。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .models import DocType


PROGRAM_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


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
    pass_threshold: float = 60.0
    min_reviewers: int = 1               # 可簽發所需的最低有效評審人數

    @property
    def rule_version(self) -> str:
        encoded = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]

    @classmethod
    def from_snapshot(cls, data: dict) -> "Program":
        """從個案快照（asdict 結果）重建計劃，供歷史個案在配置移除後查閲與匯出。"""
        program = cls(
            program_id=data["program_id"],
            name=data["name"],
            deadline=data.get("deadline", ""),
            required_docs=[RequiredDoc(**rule) for rule in data.get("required_docs", [])],
            required_fields=[RequiredField(**rule) for rule in data.get("required_fields", [])],
            criteria=[ReviewCriterion(**rule) for rule in data.get("criteria", [])],
            eligibility=Eligibility(**data.get("eligibility", {})),
            budget_cap=data.get("budget_cap"),
            divergence_threshold=data.get("divergence_threshold", 15.0),
            conflict_rules=data.get("conflict_rules", {}),
            pass_threshold=data.get("pass_threshold", 60.0),
            min_reviewers=data.get("min_reviewers", 1),
        )
        program._validate()
        return program

    def _validate(self) -> None:
        if not isinstance(self.program_id, str) or not PROGRAM_ID.fullmatch(self.program_id):
            raise ValueError("program_id 必須是有效的計劃編號。")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name 必須是非空白的計劃名稱。")
        if self.deadline:
            date.fromisoformat(self.deadline)
        _number(self.pass_threshold, "pass_threshold", lower=0, upper=100)
        _number(self.divergence_threshold, "divergence_threshold", lower=0, upper=100)
        if type(self.min_reviewers) is not int or self.min_reviewers < 1:
            raise ValueError("min_reviewers 必須是不小於 1 的整數。")
        if self.budget_cap is not None:
            _number(self.budget_cap, "budget_cap", lower=0)

        # Each declaration should resolve to exactly one field, attachment or score dimension.
        _unique((rule.key for rule in self.required_fields), "required_fields")
        _unique((rule.name for rule in self.required_docs), "required_docs")
        _unique((rule.name for rule in self.criteria), "criteria")
        for criterion in self.criteria:
            _number(criterion.weight, "weight", lower=0, strictly_above=True)
            _number(criterion.max_score, "max_score", lower=0, strictly_above=True)
        for rule in self.required_fields:
            if rule.ftype not in {"str", "number", "email", "list", "date"}:
                raise ValueError(f"不支援的欄位類型 {rule.ftype}。")
            if rule.min_value is not None:
                _number(rule.min_value, "min_value")
            if rule.max_value is not None:
                _number(rule.max_value, "max_value", lower=rule.min_value)
        for rule in self.required_docs:
            if rule.doc_type not in {"any", *(kind.value for kind in DocType)}:
                raise ValueError(f"不支援的附件類型 {rule.doc_type}。")
            if not isinstance(rule.required, bool):
                raise ValueError("required 必須是布林值。")
        eligibility = self.eligibility
        if (type(eligibility.min_team) is not int or type(eligibility.max_team) is not int
                or not 0 <= eligibility.min_team <= eligibility.max_team):
            raise ValueError("團隊人數上下限須為有效整數。")
        _string_list(eligibility.allowed_org_types, "allowed_org_types")
        _string_list(eligibility.excluded_keywords, "excluded_keywords")
        if not isinstance(self.conflict_rules, dict):
            raise ValueError("conflict_rules 必須是以評審員為鍵的設定。")
        for reviewer, keywords in self.conflict_rules.items():
            _string_list([reviewer], "reviewer")
            _string_list(keywords, "conflict_rules")

    @staticmethod
    def load(path: str | Path) -> "Program":
        try:
            raw: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8-sig"))
            if not isinstance(raw, dict):
                raise ValueError("計劃設定必須是 YAML 物件。")
            deadline = raw.get("deadline", "")
            program = Program(
                program_id=raw["program_id"],
                name=raw["name"],
                deadline=deadline.isoformat() if isinstance(deadline, date) else deadline,
                required_docs=[RequiredDoc(**rule) for rule in raw.get("required_docs", [])],
                required_fields=[RequiredField(**rule) for rule in raw.get("required_fields", [])],
                criteria=[ReviewCriterion(**rule) for rule in raw.get("criteria", [])],
                eligibility=Eligibility(**raw.get("eligibility", {})),
                budget_cap=raw.get("budget_cap"),
                divergence_threshold=raw.get("divergence_threshold", 15.0),
                conflict_rules=raw.get("conflict_rules", {}),
                pass_threshold=raw.get("pass_threshold", 60.0),
                min_reviewers=raw.get("min_reviewers", 1),
            )
            program._validate()
            return program
        except (KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
            raise ValueError(f"{Path(path).name} 設定有誤：{exc}") from exc


def _number(value, label: str, *, lower=None, upper=None, strictly_above: bool = False) -> None:
    if (type(value) not in {int, float} or not math.isfinite(value)
            or (lower is not None and (value < lower or (strictly_above and value == lower)))
            or (upper is not None and value > upper)):
        raise ValueError(f"{label} 超出有效數值範圍。")


def _string_list(values, label: str) -> None:
    if not isinstance(values, list) or not all(isinstance(value, str) and value.strip() for value in values):
        raise ValueError(f"{label} 必須是有效文字清單。")


def _unique(values, label: str) -> None:
    names = list(values)
    _string_list(names, label)
    if len(names) != len({name.strip().casefold() for name in names}):
        raise ValueError(f"{label} 含重複名稱。")
