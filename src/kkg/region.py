"""地區模型：以配置描述各地語言、法規、格式與評審慣例。

Agent 根據申請所指地區載入對應配置，自動切換審查視角：
- 語言與語法要求（繁體／簡體／英文、術語習慣）
- 法規與合規關鍵詞（私隱、資料保護、知識產權等）
- 日期、貨幣、編號等本地格式
- 當地資助評審的常見慣例
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
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
    timezone: str = ""                             # IANA 時區，如 Asia/Hong_Kong

    def today(self) -> "date":
        """回傳本地區的當地今天（延遲導入避免循環依賴）。"""
        from .timezone import today_in_zone
        return today_in_zone(self.timezone)

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
            timezone=raw.get("timezone", ""),
        )

    @staticmethod
    def load_all(root: str | Path) -> dict[str, "Region"]:
        return {r.code: r for r in
                (Region.load(p) for p in sorted(Path(root).glob("*.yaml")))}


def detect_script(text: str) -> str:
    """粗略判斷文字使用的字形：hant / hans / ja / ko / latin。

    以差異字與音節字符取樣，適合文件級別的判斷，不追求語言學精度。
    日文假名、韓文諺文各自構成獨立書寫系統，優先於漢字取樣判定，
    回傳 "ja"/"ko" 讓地區審查能給出符合事實的字形提示。
    """
    # 日文假名（平假名 U+3040–309F、片假名 U+30A0–30FF）
    kana = sum(1 for c in text if "\u3040" <= c <= "\u30ff")
    if kana:
        return "ja"
    # 韓文諺文（U+AC00–D7A3 及相容字母 U+3130–318F）
    hangul = sum(1 for c in text if "\uac00" <= c <= "\ud7a3" or "\u3130" <= c <= "\u318f")
    if hangul:
        return "ko"
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
