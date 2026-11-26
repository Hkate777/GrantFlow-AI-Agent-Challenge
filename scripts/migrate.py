"""P0-08 資料遷移 CLI：為舊個案補 ``program_snapshot``。

用法：
    python scripts/migrate.py --db output/grantflow.sqlite3              # 試算（預設不寫入）
    python scripts/migrate.py --db output/grantflow.sqlite3 --apply      # 實際寫入
    python scripts/migrate.py --db ... --configs configs --apply

先跑一次不放 ``--apply`` 查看報告，確認 ``unresolved`` 為空再正式執行。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from kkg.migrate import migrate_snapshots                        # noqa: E402
from kkg.program import Program                                  # noqa: E402


def _programs(directory: Path) -> dict[str, Program]:
    programs = {}
    for config in sorted(directory.glob("*.yaml")):
        program = Program.load(config)
        programs[program.program_id] = program
    return programs


def main() -> None:
    ap = argparse.ArgumentParser(description="為舊個案補計劃快照（P0-08）")
    ap.add_argument("--db", default=str(ROOT / "output" / "grantflow.sqlite3"),
                    help="個案資料庫路徑")
    ap.add_argument("--configs", default=str(ROOT / "configs"), help="計劃設定目錄")
    ap.add_argument("--apply", action="store_true", help="實際寫入；預設只試算")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"資料庫不存在：{db}")
        sys.exit(1)
    programs = _programs(Path(args.configs))
    report = migrate_snapshots(db, programs, apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.apply and report["migrated"]:
        print(f"\n以上 {report['migrated']} 筆為試算結果；加 --apply 才會寫入。")
    if report["unresolved"]:
        print("\n以下個案在當前配置找不到同名計劃，未處理，請管理員確認：")
        for item in report["unresolved"]:
            print(f"  - {item['id']}（program_id={item['program_id']}）")


if __name__ == "__main__":
    main()
