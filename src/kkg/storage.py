"""Local case storage with serialized writes and revision checks."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class MissingCase(LookupError):
    pass


class WorkspaceConflict(RuntimeError):
    pass


class CaseStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def transaction(self, *, write: bool = False):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            connection.execute("""CREATE TABLE IF NOT EXISTS cases (
                id TEXT PRIMARY KEY,
                program_id TEXT NOT NULL,
                application_key TEXT NOT NULL,
                payload TEXT NOT NULL,
                UNIQUE (program_id, application_key)
            )""")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        finally:
            connection.close()

    @staticmethod
    def list(connection: sqlite3.Connection, program_id: str | None = None) -> list[dict]:
        if program_id is None:
            rows = connection.execute("SELECT payload FROM cases ORDER BY rowid DESC")
        else:
            rows = connection.execute("SELECT payload FROM cases WHERE program_id = ? ORDER BY rowid DESC",
                                      (program_id,))
        return [json.loads(row[0]) for row in rows]

    @staticmethod
    def find(connection: sqlite3.Connection, program_id: str, app_id: str) -> dict | None:
        row = connection.execute("SELECT payload FROM cases WHERE program_id = ? AND application_key = ?",
                                 (program_id, app_id.casefold())).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def get(connection: sqlite3.Connection, case_id: str) -> dict:
        row = connection.execute("SELECT payload FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            raise MissingCase("找不到申請個案。")
        return json.loads(row[0])

    @staticmethod
    def insert(connection: sqlite3.Connection, case: dict) -> None:
        app = case["application"]
        connection.execute("INSERT INTO cases VALUES (?, ?, ?, ?)",
                           (case["id"], app["program"], app["app_id"].casefold(), _encode(case)))

    @staticmethod
    def update(connection: sqlite3.Connection, case: dict) -> None:
        connection.execute("UPDATE cases SET payload = ? WHERE id = ?", (_encode(case), case["id"]))


def _encode(case: dict) -> str:
    return json.dumps(case, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
