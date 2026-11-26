"""資料遷移腳本（P0-08）：為升級前缺少 ``program_snapshot`` 的舊個案補快照。

背景
----
``program_snapshot`` 自本版起在收件時寫入個案。升級前的舊個案沒有這個鍵，
一旦計劃設定被移除或改版，這些個案在查閱/匯出時會因找不到當前計劃而 422。
本腳本用**當前配置**中同名計劃的快照回填，讓舊個案恢復可讀。

邊界
----
- 只補「沒有 snapshot 鍵」或「snapshot 為空」的個案；已有快照一律不覆蓋，
  避免改寫簽發時鎖定的規則版本。
- 當前配置找不到同名計劃時記入 ``unresolved``，交管理員決定，不臆造快照。
- 預設 ``dry_run=True``：只報告將變更多少筆，不寫入。``apply=True`` 才落盤。
- 整個過程在單一事務內完成，要嘛全補、要嘛全不補。
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from .program import Program
from .storage import CaseStore


def migrate_snapshots(database: str | Path, programs: dict[str, Program], *,
                      apply: bool = False) -> dict:
    """回填舊個案的 ``program_snapshot``。

    回傳報告字典：``total`` / ``already_ok`` / ``migrated`` / ``unresolved`` /
    ``applied``（是否真的寫入）。
    """
    store = CaseStore(database)
    report = {"total": 0, "already_ok": 0, "migrated": 0,
              "unresolved": [], "applied": apply}
    with store.transaction(write=apply) as connection:
        rows = connection.execute("SELECT id, payload FROM cases").fetchall()
        import json
        for case_id, payload in rows:
            report["total"] += 1
            case = json.loads(payload)
            if case.get("program_snapshot"):
                report["already_ok"] += 1
                continue
            program_id = (case.get("application") or {}).get("program")
            program = programs.get(program_id)
            if program is None:
                report["unresolved"].append({"id": case_id, "program_id": program_id})
                continue
            case["program_snapshot"] = asdict(program)
            if apply:
                connection.execute("UPDATE cases SET payload = ? WHERE id = ?",
                                   (json.dumps(case, ensure_ascii=False, allow_nan=False),
                                    case_id))
            report["migrated"] += 1
    return report
