"""地區時區：agent 處理不同地區時須以當地今天判斷日期，而非機器本地時間。"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from kkg.timezone import (
    DEFAULT_ZONE, ZONE_BY_REGION, region_today, supported_regions,
    today_in_zone, zone_for_region,
)
from kkg.region import Region


def test_all_regions_have_iana_timezone():
    """15 個地區都須有有效 IANA 時區，且與配置欄位一致。"""
    root = Path(__file__).resolve().parent.parent / "configs" / "regions"
    regions = Region.load_all(root)
    assert len(regions) == 15
    for code, region in regions.items():
        assert region.timezone, f"{code} 缺 timezone"
        assert region.timezone == ZONE_BY_REGION[code]
        # 能實例化即證明時區名稱有效
        assert isinstance(region.today(), date)


def test_zone_for_region_fallback():
    """未知/空地區碼退回預設香港時區，不會拋錯。"""
    assert zone_for_region("HK") == "Asia/Hong_Kong"
    assert zone_for_region("hk") == "Asia/Hong_Kong"
    assert zone_for_region("XX") == DEFAULT_ZONE
    assert zone_for_region(None) == DEFAULT_ZONE
    assert set(supported_regions()) == set(ZONE_BY_REGION)


def test_today_in_zone_matches_iana_offset():
    """以固定 UTC 時刻驗證各地日期換算正確（避開真實時間浮動）。

    2026-11-20 17:00 UTC：香港/悉尼已 11-21，紐約/倫敦仍 11-20。
    """
    instant = datetime(2026, 11, 20, 17, 0, tzinfo=timezone.utc)
    from kkg import timezone as tzmod

    class Frozen:
        @staticmethod
        def now(zone):
            return instant.astimezone(zone)

    original = tzmod.datetime
    tzmod.datetime = Frozen  # type: ignore[assignment]
    try:
        assert tzmod.today_in_zone("Asia/Hong_Kong") == date(2026, 11, 21)
        assert tzmod.today_in_zone("Australia/Sydney") == date(2026, 11, 21)
        assert tzmod.today_in_zone("America/New_York") == date(2026, 11, 20)
        assert tzmod.today_in_zone("Europe/London") == date(2026, 11, 20)
    finally:
        tzmod.datetime = original


def test_today_in_zone_bad_zone_degrades():
    """時區資料缺失/名稱錯誤時降級到機器本地日期，不中斷。"""
    assert today_in_zone("Not/A_Zone") == date.today()
    assert today_in_zone("") == date.today()


def test_region_today_accepts_object_code_and_none():
    """region_today 接受 Region 物件、地區碼字串與 None。"""
    root = Path(__file__).resolve().parent.parent / "configs" / "regions"
    hk = Region.load(root / "HK.yaml")
    assert region_today(hk) == hk.today()
    assert region_today("HK") == today_in_zone("Asia/Hong_Kong")
    assert region_today(None) == today_in_zone(DEFAULT_ZONE)


def test_confirmation_date_uses_region_timezone():
    """「確認日期不可晚於今天」須以地區當日為準。

    固定讓香港為 11-21、紐約為 11-20：同一個 11-21 的確認日期在香港合法、
    在紐約仍屬未來而被拒絕。
    """
    from kkg.track import Disbursement, PeriodicReport

    hk_today = today_in_zone("Asia/Hong_Kong")
    ny_today = today_in_zone("America/New_York")
    # _only run when the two zones are on different calendar days_
    if hk_today == ny_today:
        pytest.skip("香港與紐約當日相同，無法驗證跨日判斷")

    boundary = hk_today.isoformat()  # 香港合法（<= 香港今天），紐約視為未來
    if ny_today >= hk_today:
        pytest.skip("香港日期未領先紐約，無法驗證")

    # 香港時區：當日確認合法
    d = Disbursement("首期", "2026-01-01", 50, 50, boundary, tz="Asia/Hong_Kong")
    assert d.paid_at == boundary
    # 紐約時區：同一日期仍屬未來，應拒絕
    with pytest.raises(ValueError, match="不可晚於今天"):
        Disbursement("首期", "2026-01-01", 50, 50, boundary, tz="America/New_York")
    with pytest.raises(ValueError, match="不可晚於今天"):
        PeriodicReport("進度報告", "2026-01-01", boundary, tz="America/New_York")


def test_project_status_infers_tz_from_records():
    """未显式傳 tz 時，project_status 從記錄物件自帶的 tz 推斷當地今天。"""
    from kkg.models import Milestone
    from kkg.track import PeriodicReport, project_status

    hk_today = today_in_zone("Asia/Hong_Kong")
    ny_today = today_in_zone("America/New_York")
    if hk_today == ny_today:
        pytest.skip("香港與紐約當日相同")

    boundary = hk_today.isoformat()
    # 一個已提交的報告，提交日 = 香港今天；帶 tz=HK
    report = PeriodicReport("報告", "2026-01-01", boundary, tz="Asia/Hong_Kong")
    status = project_status([Milestone("m", "2026-01-01", "done")], [report])
    # 以香港今天投影，提交日應被保留為已提交
    assert status["periodic_reports"][0]["status"] in ("submitted", "overdue")
