"""港幣金額的嚴格解析與顯示。"""
from __future__ import annotations

import math
import re
import unicodedata
from decimal import Decimal

_UNITS = {
    "千": 1_000, "k": 1_000, "thousand": 1_000,
    "萬": 10_000, "\u4e07": 10_000,
    "百萬": 1_000_000, "百\u4e07": 1_000_000, "m": 1_000_000, "million": 1_000_000,
    "億": 100_000_000, "\u4ebf": 100_000_000,
    "b": 1_000_000_000, "billion": 1_000_000_000,
}
_CURRENCY = r"(?:HKD\s*\$?|HK\s*\$|\$|港幣|港\u5e01|港元|港圓|港\u5706)"
_HKD_AMOUNT = re.compile(
    rf"(?:{_CURRENCY}\s*)?"
    r"(?P<number>[+-]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?)"
    rf"\s*(?P<unit>{'|'.join(_UNITS)})?\s*"
    rf"(?:{_CURRENCY}|元整?)?",
    re.IGNORECASE,
)


def parse_hkd(value: object) -> float | None:
    """將數值與指定為港幣的金額文字轉成有限數值。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        match = _HKD_AMOUNT.fullmatch(unicodedata.normalize("NFKC", value).strip())
        if match is None:
            return None
        unit = (match["unit"] or "").casefold()
        amount = float(match["number"].replace(",", "")) * _UNITS.get(unit, 1)
    elif isinstance(value, (int, float, Decimal)):
        try:
            amount = float(value)
        except (ValueError, OverflowError):
            return None
    else:
        return None
    return amount if math.isfinite(amount) else None


def format_hkd(amount: float) -> str:
    return f"HK${amount:,.2f}" if amount % 1 else f"HK${amount:,.0f}"
