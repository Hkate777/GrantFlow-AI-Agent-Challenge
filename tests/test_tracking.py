"""Regression tests for post-award tracking."""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kkg import track  # noqa: E402
from kkg.models import Milestone  # noqa: E402


TODAY = date(2026, 11, 20)


def test_milestone_deadline_boundaries_leave_records_unchanged():
    milestones = [
        Milestone("已完成", "2026-11-01", "done"),
        Milestone("昨日", "2026-11-19"),
        Milestone("今日", "2026-11-20"),
        Milestone("十四天後", "2026-12-04"),
        Milestone("十五天後", "2026-12-05", "overdue"),
        Milestone("日期錯誤", "2026-02-31"),
    ]

    status = track.project_status(milestones, today=TODAY)

    assert [row["status"] for row in status["milestones"]] == [
        "done", "overdue", "pending", "pending", "pending", "pending",
    ]
    assert [m.status for m in milestones] == [
        "done", "pending", "pending", "pending", "overdue", "pending",
    ]
    assert len(status["alerts"]) == 4
    for name, message in [("昨日", "逾期 1 天"), ("今日", "今天到期"),
                          ("十四天後", "14 天後到期"), ("日期錯誤", "日期格式異常")]:
        assert any(f"「{name}」" in alert and message in alert for alert in status["alerts"])


def test_legacy_milestone_check_mutates_only_overdue_and_honours_custom_warning():
    milestones = [
        Milestone("今日", "2026-11-20"),
        Milestone("明日", "2026-11-21"),
        Milestone("舊日期格式", "2026-1-1", funding_release=1250),
        Milestone("已逾期記錄", "2026-12-20", "overdue"),
    ]

    alerts = track.check_milestones(milestones, TODAY, 0)

    assert len(alerts) == 2
    assert "今日" in alerts[0]
    assert "舊日期格式" in alerts[1] and "HK$1,250" in alerts[1]
    assert [m.status for m in milestones] == ["pending", "pending", "overdue", "overdue"]


def test_disbursements_count_actual_payments_and_keep_partial_payment_overdue():
    milestones = [Milestone("已完成", "2026-11-01", "done", 1000)]
    disbursements = [
        track.Disbursement("首期", "2026-11-19", 700.15, 200.2, "2026-11-18"),
        track.Disbursement("第二期", "2026-11-18", 100.1, 100.1, "2026-11-19"),
        track.Disbursement("第三期", "2026-12-04", 10),
        track.Disbursement("第四期", "2026-12-05", 30),
    ]

    status = track.project_status(milestones, disbursements=disbursements, today=TODAY)

    assert status["funding"] == {"scheduled": 840.25, "paid": 300.3}
    assert [row["status"] for row in status["disbursements"]] == [
        "partial", "paid", "pending", "pending",
    ]
    assert [row["overdue"] for row in status["disbursements"]] == [True, False, False, False]
    assert len(status["alerts"]) == 2
    assert "首期" in status["alerts"][0] and "逾期 1 天" in status["alerts"][0]
    assert "第三期" in status["alerts"][1] and "14 天後到期" in status["alerts"][1]


def test_future_confirmations_are_rejected_at_construction():
    """P0-11：未來日期的實付/提交確認在構造時即拒絕——舊設計（投影時靜默清空）
    會造成同一畫面二次保存丟失原輸入，已由構造期攔截取代。"""
    import pytest

    with pytest.raises(ValueError, match="不可晚於今天"):
        track.Disbursement("首期", "2026-11-18", 50, 50, "2026-11-21")
    with pytest.raises(ValueError, match="不可晚於今天"):
        track.PeriodicReport("進度報告", "2026-11-18", "2026-11-21")


def test_periodic_reports_warn_until_submitted_and_keep_legacy_notes_unconfirmed():
    reports = [
        track.PeriodicReport("月度報告", "2026-11-19"),
        track.PeriodicReport("季度報告", "2026-12-04"),
        track.PeriodicReport("年度報告", "2026-12-05"),
        track.PeriodicReport("成果報告", "2026-11-18", "2026-11-19"),
        "歷史月報：已提交",
    ]

    status = track.project_status([], reports, today=TODAY)

    assert [row["status"] for row in status["periodic_reports"]] == [
        "overdue", "pending", "pending", "submitted", "unknown",
    ]
    assert status["periodic_reports"][-1]["name"] == "歷史月報：已提交"
    assert len(status["alerts"]) == 2
    assert "月度報告" in status["alerts"][0] and "逾期 1 天" in status["alerts"][0]
    assert "季度報告" in status["alerts"][1] and "14 天後到期" in status["alerts"][1]


@pytest.mark.parametrize("amount,paid_amount,paid_at", [
    (0, 0, ""),
    (-10, 0, ""),
    (float("nan"), 0, ""),
    (True, 0, ""),
    ("10", 0, ""),
    (10, -1, "2026-11-19"),
    (10, float("inf"), "2026-11-19"),
    (10, 11, "2026-11-19"),
    (10, 1, ""),
    (10, 0, "2026-11-19"),
])
def test_disbursement_rejects_invalid_amounts_and_incomplete_receipts(amount, paid_amount, paid_at):
    with pytest.raises(ValueError):
        track.Disbursement("首期", "2026-11-20", amount, paid_amount, paid_at)


@pytest.mark.parametrize("invalid_date", ["", "2026-02-29", "20261120", "2026-1-20"])
def test_tracking_records_require_iso_due_dates(invalid_date):
    with pytest.raises(ValueError):
        track.Disbursement("首期", invalid_date, 10)
    with pytest.raises(ValueError):
        track.PeriodicReport("進度報告", invalid_date)


def test_tracking_records_reject_invalid_receipt_and_submission_dates():
    with pytest.raises(ValueError):
        track.Disbursement("首期", "2026-11-20", 10, 10, "2026-11-31")
    with pytest.raises(ValueError):
        track.PeriodicReport("進度報告", "2026-11-20", "2026-11-31")


def test_legacy_schedule_keeps_planned_funding_without_assuming_payment():
    milestones = [
        Milestone("啓動", "2026-11-01", "done", 50),
        Milestone("驗收", "2026-11-19", funding_release=75),
    ]

    status = track.project_status(milestones, disbursements=[], today=TODAY)
    report = track.management_report("APP-1", "示範", milestones, ["歷史月報：已提交"], today=TODAY)

    assert status["funding"] == {"scheduled": 125, "paid": 0}
    assert report.startswith("# 項目管理報告 · APP-1")
    assert "2026-11-20" in report
    assert "計劃發放：HK$125.00" in report
    assert "已確認發放：HK$0.00" in report
    assert any("歷史月報：已提交" in line and "待核實" in line for line in report.splitlines())
    assert milestones[1].status == "pending"


def test_management_report_shows_payment_and_submission_evidence():
    payment = track.Disbursement("第一期", "2026-11-18", 1000, 249.5, "2026-11-17")
    submitted = track.PeriodicReport("首份報告", "2026-11-20", "2026-11-19")
    report = track.management_report("APP-1", "示範", [], [submitted],
                                     disbursements=[payment], today=TODAY)

    assert "已確認發放：HK$249.50" in report
    assert any("第一期" in line and "部分發放" in line and "2026-11-17" in line
               for line in report.splitlines())
    assert any("首份報告" in line and "已提交" in line and "2026-11-19" in line
               for line in report.splitlines())


def test_standalone_funding_schedule_keeps_completed_milestone_payment_unconfirmed():
    schedule = track.funding_schedule([Milestone("啓動", "2026-11-01", "done", 50)])

    assert "啓動" in schedule
    assert "計劃發放：HK$50.00" in schedule
    assert "已確認發放：HK$0.00" in schedule
