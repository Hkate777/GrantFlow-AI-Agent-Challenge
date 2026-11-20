#!/usr/bin/env python3
"""加密密鑰環管理：新增/讀取/列出/刪除模型 API 憑據，明文不落盤。

主口令由環境變數 GRANTFLOW_VAULT_PASS 提供（建議在 shell 設定，不要寫入
腳本或版本控制）。密鑰環檔案路徑可用 GRANTFLOW_VAULT_PATH 指定，
預設 output/.secrets.vault.json（已被 .gitignore 排除）。

用法：
    export GRANTFLOW_VAULT_PASS='你的主口令'
    python scripts/vault_cli.py set OPENAI_API_KEY sk-xxx...
    python scripts/vault_cli.py list
    python scripts/vault_cli.py get OPENAI_API_KEY        # 僅顯示遮蔽值
    python scripts/vault_cli.py get OPENAI_API_KEY --reveal  # 顯示明文（慎用）
    python scripts/vault_cli.py delete OPENAI_API_KEY
    python scripts/vault_cli.py verify                     # 驗證口令與完整性
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from kkg.secrets import SecretVault, VaultError, apply_vault_to_env  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="GrantFlow 加密密鑰環管理")
    ap.add_argument("action", choices=["set", "get", "list", "delete", "verify", "inject"])
    ap.add_argument("name", nargs="?", help="憑據名稱，如 OPENAI_API_KEY")
    ap.add_argument("value", nargs="?", help="set 時的憑據值")
    ap.add_argument("--reveal", action="store_true", help="get 時顯示明文（預設只顯示遮蔽值）")
    args = ap.parse_args()

    try:
        vault = SecretVault()
    except VaultError as exc:
        raise SystemExit(f"[錯誤] {exc}")

    try:
        if args.action == "set":
            if not args.name or not args.value:
                raise SystemExit("用法：vault_cli.py set <名稱> <值>")
            vault.set(args.name, args.value)
            print(f"已儲存憑據：{args.name}（遮蔽：{vault.mask(args.value)}）")

        elif args.action == "get":
            if not args.name:
                raise SystemExit("用法：vault_cli.py get <名稱>")
            value = vault.get(args.name)
            if value is None:
                raise SystemExit(f"找不到憑據：{args.name}")
            print(value if args.reveal else f"{args.name} = {vault.mask(value)}（加 --reveal 顯示明文）")

        elif args.action == "list":
            keys = vault.listing()
            if not keys:
                print("密鑰環為空。")
            else:
                print(f"密鑰環：{vault.path}")
                for item in keys:
                    print(f"  {item['name']} = {item['value']}")

        elif args.action == "delete":
            if not args.name:
                raise SystemExit("用法：vault_cli.py delete <名稱>")
            print("已刪除。" if vault.delete(args.name) else f"找不到憑據：{args.name}")

        elif args.action == "verify":
            vault.verify()
            print(f"口令正確、檔案完整。共 {len(vault.names())} 項憑據。")

        elif args.action == "inject":
            injected = apply_vault_to_env(vault)
            print(f"已注入進程環境的憑據：{'、'.join(injected) or '無'}")
    except VaultError as exc:
        raise SystemExit(f"[錯誤] {exc}")


if __name__ == "__main__":
    main()
