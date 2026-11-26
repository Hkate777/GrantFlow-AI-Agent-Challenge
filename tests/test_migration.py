"""P0-08 遷移回歸：舊個案補 program_snapshot，冪等且 dry-run 不寫入。"""
from __future__ import annotations

import json

from kkg.migrate import migrate_snapshots
from kkg.program import Program
from kkg.storage import CaseStore


def _programs() -> dict[str, Program]:
    from pathlib import Path
    import sys
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    return {"HK-ACC-2027": Program.load(root / "configs" / "program_demo.yaml")}


def _insert_case(store: CaseStore, case_id: str, *, with_snapshot: bool,
                 program_id: str = "HK-ACC-2027") -> None:
    case = {
        "id": case_id,
        "application": {"program": program_id, "app_id": case_id.lower(),
                        "app_name": "T", "applicant": "A", "email": "a@b.test",
                        "team": [], "fields": {}, "docs": []},
        "channel": "web", "revision": 1, "received_at": "2026-01-01T00:00:00+00:00",
        "assignments": [], "scores": [], "audit": [],
        "project": {"milestones": [], "periodic_reports": [], "disbursements": []},
    }
    if with_snapshot:
        case["program_snapshot"] = {"marker": "original"}
    with store.transaction(write=True) as connection:
        store.insert(connection, case)


def _has_snapshot(db, case_id: str) -> bool:
    with CaseStore(db).transaction() as connection:
        case = connection.execute("SELECT payload FROM cases WHERE id = ?", (case_id,)).fetchone()
    return bool(json.loads(case[0]).get("program_snapshot"))


def test_dry_run_reports_without_writing(tmp_path):
    db = str(tmp_path / "c.sqlite3")
    store = CaseStore(db)
    _insert_case(store, "OLD-1", with_snapshot=False)
    report = migrate_snapshots(db, _programs(), apply=False)
    assert report["total"] == 1
    assert report["migrated"] == 1
    assert report["applied"] is False
    assert _has_snapshot(db, "OLD-1") is False


def test_apply_fills_snapshot_and_is_idempotent(tmp_path):
    db = str(tmp_path / "c.sqlite3")
    store = CaseStore(db)
    _insert_case(store, "OLD-1", with_snapshot=False)
    _insert_case(store, "NEW-1", with_snapshot=True)

    first = migrate_snapshots(db, _programs(), apply=True)
    assert first["migrated"] == 1
    assert first["already_ok"] == 1
    assert _has_snapshot(db, "OLD-1") is True
    with store.transaction() as connection:
        old = connection.execute("SELECT payload FROM cases WHERE id = 'OLD-1'").fetchone()
    snapshot = json.loads(old[0])["program_snapshot"]
    assert snapshot["program_id"] == "HK-ACC-2027"

    # 再跑一次：已有快照的不動，migrated 歸零。
    second = migrate_snapshots(db, _programs(), apply=True)
    assert second["migrated"] == 0
    assert second["already_ok"] == 2


def test_unresolved_program_is_reported_not_guessed(tmp_path):
    db = str(tmp_path / "c.sqlite3")
    store = CaseStore(db)
    _insert_case(store, "GONE-1", with_snapshot=False, program_id="REMOVED-PLAN")
    report = migrate_snapshots(db, _programs(), apply=True)
    assert report["migrated"] == 0
    assert report["unresolved"] == [{"id": "GONE-1", "program_id": "REMOVED-PLAN"}]
    assert _has_snapshot(db, "GONE-1") is False


def test_original_snapshot_not_overwritten(tmp_path):
    """已有快照（即使看似舊格式）不被當前配置覆蓋，保護簽發時鎖定的規則版本。"""
    db = str(tmp_path / "c.sqlite3")
    store = CaseStore(db)
    _insert_case(store, "SIGNED-1", with_snapshot=True)
    migrate_snapshots(db, _programs(), apply=True)
    with store.transaction() as connection:
        row = connection.execute("SELECT payload FROM cases WHERE id = 'SIGNED-1'").fetchone()
    assert json.loads(row[0])["program_snapshot"] == {"marker": "original"}
