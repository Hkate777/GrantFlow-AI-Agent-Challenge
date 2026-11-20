"""地區時區工具：把「今天」由執行機器的本地時間，改為申請所屬地區的當地日期。

同一瞬間各地日期可能不同（例如香港已 11-21、紐約仍 11-20），若用伺服器
本地時間判斷到期或「確認日期是否晚於今天」，會對跨區申請給出錯誤結果。
本模組提供統一的地區碼 -> IANA 時區映射與當地今天計算，並在系統缺少
tzdata 時安全降級到機器本地日期，不中斷流程。
"""
from __future__ import annotations

from datetime import date, datetime

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # Python < 3.9 的極端降級
    ZoneInfo = None  # type: ignore[assignment]
    ZoneInfoNotFoundError = Exception  # type: ignore[assignment,misc]


# 地區碼 -> IANA 時區名稱。與 configs/regions/*.yaml 的 timezone 欄位一致；
# 此表為沒有 Region 物件時（如純 CLI 傳地區碼）的權威來源。
ZONE_BY_REGION: dict[str, str] = {
    "HK": "Asia/Hong_Kong",
    "TW": "Asia/Taipei",
    "CN": "Asia/Shanghai",
    "SG": "Asia/Singapore",
    "JP": "Asia/Tokyo",
    "KR": "Asia/Seoul",
    "GB": "Europe/London",
    "US": "America/New_York",
    "CA": "America/Toronto",
    "AU": "Australia/Sydney",
    "AE": "Asia/Dubai",
    "SA": "Asia/Riyadh",
    "EG": "Africa/Cairo",
    "MY": "Asia/Kuala_Lumpur",
    "TH": "Asia/Bangkok",
}

DEFAULT_ZONE = "Asia/Hong_Kong"


def supported_regions() -> list[str]:
    """回傳所有已定義時區的地區碼（排序）。"""
    return sorted(ZONE_BY_REGION)


def zone_for_region(code: str | None) -> str:
    """地區碼轉 IANA 時區；未知地區退回香港時區。"""
    if not code:
        return DEFAULT_ZONE
    return ZONE_BY_REGION.get(code.upper(), DEFAULT_ZONE)


def today_in_zone(zone: str | None) -> date:
    """回傳指定 IANA 時區的當地今天；時區資料缺失時降級到機器本地日期。"""
    if not zone or ZoneInfo is None:
        return date.today()
    try:
        return datetime.now(ZoneInfo(zone)).date()
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return date.today()


def region_today(region: object | str | None) -> date:
    """取得地區對應的當地今天。

    接受三種輸入：Region 物件（讀其 timezone 欄位）、地區碼字串、或 None。
    讓上層無須自行判斷傳的是物件還是代碼。
    """
    if region is None:
        return today_in_zone(DEFAULT_ZONE)
    if isinstance(region, str):
        return today_in_zone(zone_for_region(region))
    zone = getattr(region, "timezone", "") or zone_for_region(getattr(region, "code", None))
    return today_in_zone(zone)
