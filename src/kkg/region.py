"""地區模型：以配置描述各地語言、法規、格式與評審慣例。

Agent 根據申請所指地區載入對應配置，自動切換審查視角：
- 語言與語法要求（繁體／簡體／英文、術語習慣）
- 法規與合規關鍵詞（私隱、資料保護、知識產權等）
- 日期、貨幣、編號等本地格式
- 當地資助評審的常見慣例
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Region:
    code: str                                  # 地區代碼，如 HK / GB / AU / AE，見 configs/regions/
    name: str
    languages: list[str] = field(default_factory=list)   # 優先語言，例如 ["zh-Hant", "en"]
    script: str = "hant"                       # hant / hans / latin
    locale_terms: dict[str, str] = field(default_factory=dict)  # 非本地用詞 -> 本地用詞
    legal_keywords: list[str] = field(default_factory=list)     # 當地合規必要提及
    date_formats: list[str] = field(default_factory=list)       # 接受的日期格式說明
    currency: str = ""
    review_conventions: list[str] = field(default_factory=list)  # 評審慣例提示

    @staticmethod
    def load(path: str | Path) -> "Region":
        raw: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return Region(
            code=raw["code"], name=raw["name"],
            languages=raw.get("languages", []), script=raw.get("script", "hant"),
            locale_terms=raw.get("locale_terms", {}),
            legal_keywords=raw.get("legal_keywords", []),
            date_formats=raw.get("date_formats", []),
            currency=raw.get("currency", ""),
            review_conventions=raw.get("review_conventions", []),
        )

    @staticmethod
    def load_all(root: str | Path) -> dict[str, "Region"]:
        return {r.code: r for r in
                (Region.load(p) for p in sorted(Path(root).glob("*.yaml")))}


def detect_script(text: str) -> str:
    """粗略判斷文字使用的字形：hant / hans / latin。

    以一組差異字取樣，適合文件級別的判斷，不追求語言學精度。
    """
    hant_markers = "體網絡資料軟件資訊與對於為這個們時後點嗎麼"
    hans_markers = "体网络资料软件信息与对于为这个们时后点吗么"
    cjk = [c for c in text if "\u4e00" <= c <= "\u9fff"]
    if not cjk:
        return "latin"
    a = sum(c in hant_markers for c in cjk)
    b = sum(c in hans_markers for c in cjk)
    if a > b:
        return "hant"
    if b > a:
        return "hans"
    return "hant"  # 無法判斷時偏保守，按繁體處理
