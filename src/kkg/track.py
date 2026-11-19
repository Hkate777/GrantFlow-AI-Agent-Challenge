"""項目追蹤 Agent：里程碑、資助發放、定期報告跟蹤 + 異常預警 + 管理報告。"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .models import Milestone


def check_milestones(ms: list[Milestone], today: date | None = None,
                     warn_days: int = 14) -> list[str]:
    today = today or date.today()
    alerts: list[str] = []
    for m in ms:
        try:
            due = datetime.strptime(m.due, "%Y-%m-%d").date()
        except ValueError:
            alerts.append(f"⚠️ 里程碑「{m.name}」日期格式異常（{m.due}）")
            continue
        if m.status == "done":
            continue
        if due < today:
            m.status = "overdue"
            alerts.append(f"🔴 里程碑「{m.name}」已逾期 {abs((due - today).days)} 天"
                          + (f"，關聯資助發放 HK${m.funding_release:,.0f}" if m.funding_release else ""))
        elif due - today <= timedelta(days=warn_days):
            alerts.append(f"🟡 里程碑「{m.name}」將於 {(due - today).days} 天后到期（{m.due}）")
    return alerts


def funding_schedule(ms: list[Milestone]) -> str:
    lines = ["| 里程碑 | 到期 | 狀態 | 關聯資助 (HKD) |", "|---|---|---|---|"]
    for m in ms:
        lines.append(f"| {m.name} | {m.due} | {m.status} | {m.funding_release:,.0f} |")
    total = sum(m.funding_release for m in ms)
    released = sum(m.funding_release for m in ms if m.status == "done")
    lines.append(f"\n**發放進度：{released:,.0f} / {total:,.0f}**")
    return "\n".join(lines)


def management_report(app_id: str, title: str, ms: list[Milestone],
                      periodic_reports: list[str] | None = None) -> str:
    alerts = check_milestones(ms)
    lines = [
        f"# 項目管理報告 · {app_id}",
        "",
        f"項目：{title}",
        f"報告日期：{date.today().isoformat()}",
        "",
        "## 資助發放",
        funding_schedule(ms),
        "",
        "## 預警",
    ]
    lines += [f"- {a}" for a in alerts] if alerts else ["- ✅ 無預警，進度正常。"]
    lines += ["", "## 定期報告提交情況"]
    if periodic_reports:
        lines += [f"- {r}" for r in periodic_reports]
    else:
        lines.append("- （暫無定期報告記錄）")
    return "\n".join(lines)
