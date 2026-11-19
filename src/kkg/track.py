"""項目追蹤、資助發放、定期報告與到期預警。"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from math import isfinite

from .models import Milestone


WARN_DAYS = 14


def _validate_date(value: str, field: str) -> None:
    error = f"{field}須為有效的 YYYY-MM-DD 日期"
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(error) from None
    if parsed.isoformat() != value:
        raise ValueError(error)


def _validate_not_future(value: str, field: str) -> None:
    """確認類日期（實付/提交）不可晚於今天——未來的「已發生」記錄是輸入錯誤，
    靜默接受會在讀取投影時被清空，造成同一畫面二次保存丟失原輸入。"""
    if date.fromisoformat(value) > date.today():
        raise ValueError(f"{field}不可晚於今天（{date.today().isoformat()}）——未來的確認紀錄屬輸入錯誤。")


def _amount(value: float, field: str) -> Decimal:
    error = f"{field}須為有限的非負數字"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(error)
    amount = Decimal(value) if isinstance(value, int) else Decimal(str(value))
    if not isfinite(amount) or amount < 0:
        raise ValueError(error)
    return amount


@dataclass(frozen=True)
class Disbursement:
    """每期的計劃發放金額和已確認發放記錄。"""

    name: str
    due: str
    amount: float
    paid_amount: float = 0.0
    paid_at: str = ""

    def __post_init__(self) -> None:
        _validate_date(self.due, "發放到期日")
        amount = _amount(self.amount, "計劃發放金額")
        paid = _amount(self.paid_amount, "已發放金額")
        if amount == 0 or paid > amount:
            raise ValueError("計劃發放金額須大於零，已發放金額不得超過計劃金額")
        if self.paid_at != "":
            _validate_date(self.paid_at, "發放日期")
            _validate_not_future(self.paid_at, "發放日期")
        if (paid > 0) != bool(self.paid_at):
            raise ValueError("請同時填寫已發放金額和發放日期")


@dataclass(frozen=True)
class PeriodicReport:
    """定期報告的到期日及確認提交日期。"""

    name: str
    due: str
    submitted_at: str = ""

    def __post_init__(self) -> None:
        _validate_date(self.due, "報告到期日")
        if self.submitted_at != "":
            _validate_date(self.submitted_at, "提交日期")
            _validate_not_future(self.submitted_at, "提交日期")


def _deadline_alert(label: str, due: date, today: date, warn_days: int = WARN_DAYS) -> str | None:
    days = (due - today).days
    if days < 0:
        return f"🔴 {label}已逾期 {-days} 天"
    if days > warn_days:
        return None
    if days == 0:
        return f"🟡 {label}今天到期（{due}）"
    return f"🟡 {label}將於 {days} 天後到期（{due}）"


def _milestone_state(milestone: Milestone, today: date, warn_days: int) -> tuple[str, str | None]:
    label = f"里程碑「{milestone.name}」"
    try:
        due = datetime.strptime(milestone.due, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return milestone.status, f"⚠️ {label}日期格式異常（{milestone.due}）"
    if milestone.status == "done":
        return "done", None
    alert = _deadline_alert(label, due, today, warn_days)
    if due < today:
        if milestone.funding_release:
            alert = f"{alert}，關聯計劃資助 HK${milestone.funding_release:,.0f}"
        return "overdue", alert
    return "pending", alert


def check_milestones(ms: list[Milestone], today: date | None = None,
                     warn_days: int = WARN_DAYS) -> list[str]:
    """檢查里程碑，並更新逾期記錄的狀態。"""
    today = today or date.today()
    alerts: list[str] = []
    for milestone in ms:
        status, alert = _milestone_state(milestone, today, warn_days)
        if status == "overdue":
            milestone.status = status
        if alert:
            alerts.append(alert)
    return alerts


def _confirmed_date(value: str, today: date) -> str:
    if value and date.fromisoformat(value) <= today:
        return value
    return ""


def _total_amount(values: Iterable[float]) -> float:
    total = float(sum(Decimal(str(value)) for value in values))
    if not isfinite(total):
        raise ValueError("資助金額合計超出範圍")
    return total


def project_status(milestones: list[Milestone],
                   periodic_reports: list[PeriodicReport | str] | None = None,
                   disbursements: list[Disbursement] | None = None, *,
                   today: date | None = None) -> dict:
    """按指定日期計算項目狀態，保留原始記錄。"""
    today = today or date.today()
    alerts: list[str] = []
    milestone_rows = []
    for milestone in milestones:
        status, alert = _milestone_state(milestone, today, WARN_DAYS)
        milestone_rows.append({**asdict(milestone), "status": status})
        if alert:
            alerts.append(alert)

    report_rows = []
    for report in periodic_reports or []:
        if isinstance(report, str):
            report_rows.append({"name": report, "due": "", "submitted_at": "", "status": "unknown"})
            continue
        due = date.fromisoformat(report.due)
        submitted_at = _confirmed_date(report.submitted_at, today)
        if submitted_at:
            status = "submitted"
        else:
            status = "overdue" if due < today else "pending"
            alert = _deadline_alert(f"定期報告「{report.name}」", due, today)
            if alert:
                alerts.append(alert)
        report_rows.append({**asdict(report), "submitted_at": submitted_at, "status": status})

    disbursement_rows = []
    for disbursement in disbursements or []:
        due = date.fromisoformat(disbursement.due)
        paid_at = _confirmed_date(disbursement.paid_at, today)
        paid_amount = disbursement.paid_amount if paid_at else 0.0
        outstanding = paid_amount < disbursement.amount
        if not outstanding:
            status = "paid"
        else:
            status = "partial" if paid_amount else "pending"
            alert = _deadline_alert(f"資助發放「{disbursement.name}」", due, today)
            if alert:
                alerts.append(alert)
        disbursement_rows.append({
            **asdict(disbursement),
            "paid_at": paid_at,
            "paid_amount": paid_amount,
            "status": status,
            "overdue": outstanding and due < today,
        })

    if disbursement_rows:
        scheduled = _total_amount(row["amount"] for row in disbursement_rows)
    else:
        scheduled = _total_amount(milestone.funding_release for milestone in milestones)
    return {
        "milestones": milestone_rows,
        "periodic_reports": report_rows,
        "disbursements": disbursement_rows,
        "funding": {
            "scheduled": scheduled,
            "paid": _total_amount(row["paid_amount"] for row in disbursement_rows),
        },
        "alerts": alerts,
    }


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    if not rows:
        return ["- 暫無記錄。"]
    return [
        "| " + " | ".join(" ".join(cell.splitlines()).replace("|", r"\|") for cell in row) + " |"
        for row in [headers, ["---"] * len(headers), *rows]
    ]


def _milestone_table(rows: list[dict]) -> list[str]:
    labels = {"pending": "待完成", "overdue": "逾期", "done": "已完成"}
    return _table(["里程碑", "到期日", "狀態", "關聯資助 (HKD)"], [
        [row["name"], row["due"], labels.get(row["status"], row["status"]),
         f"{row['funding_release']:,.2f}"]
        for row in rows
    ])


def _funding_totals(funding: dict[str, float]) -> str:
    return (f"**計劃發放：HK${funding['scheduled']:,.2f} · "
            f"已確認發放：HK${funding['paid']:,.2f}**")


def funding_schedule(ms: list[Milestone]) -> str:
    status = project_status(ms)
    return "\n".join([*_milestone_table(status["milestones"]), "", _funding_totals(status["funding"])])


def management_report(app_id: str, title: str, ms: list[Milestone],
                      periodic_reports: list[PeriodicReport | str] | None = None, *,
                      disbursements: list[Disbursement] | None = None,
                      today: date | None = None) -> str:
    today = today or date.today()
    status = project_status(ms, periodic_reports, disbursements, today=today)
    lines = [
        f"# 項目管理報告 · {app_id}",
        "",
        f"項目：{title}",
        f"報告日期：{today.isoformat()}",
        "",
        "## 里程碑",
        *_milestone_table(status["milestones"]),
        "",
        "## 資助發放",
        _funding_totals(status["funding"]),
        "",
    ]
    payment_labels = {"pending": "待發放", "partial": "部分發放", "paid": "已發放"}
    payment_rows = []
    for row in status["disbursements"]:
        label = payment_labels[row["status"]]
        if row["overdue"]:
            label += "（逾期）"
        payment_rows.append([row["name"], row["due"], label, f"{row['amount']:,.2f}",
                             f"{row['paid_amount']:,.2f}", row["paid_at"] or "—"])
    if not payment_rows and status["funding"]["scheduled"]:
        lines.append("計劃金額來自里程碑，實際發放待確認。")
    else:
        lines += _table(["批次", "到期日", "狀態", "計劃金額 (HKD)", "已確認金額 (HKD)", "發放日期"],
                        payment_rows)

    report_labels = {"pending": "待提交", "overdue": "逾期", "submitted": "已提交", "unknown": "待核實"}
    lines += ["", "## 定期報告提交情況"]
    lines += _table(["報告", "到期日", "狀態", "提交日期"], [
        [row["name"], row["due"] or "—", report_labels[row["status"]], row["submitted_at"] or "—"]
        for row in status["periodic_reports"]
    ])
    lines += ["", "## 預警"]
    lines += [f"- {alert}" for alert in status["alerts"]] or ["- ✅ 暫無到期預警。"]
    return "\n".join(lines)
