"""機構身份的最小實現（P0-06）：本地帳戶 + HTTP Basic + 即時停用/撤銷。

設計邊界：
- 單一機構、本機演示級。正式部署應換 OIDC/SSO（審評 P0-06 的遠期形態），
  但「服務端身分、事件綁定、停用即時生效」這三個契約自本層起成立。
- 口令以 scrypt 加鹽存儲，數據庫洩露不等於口令洩露。
- 每次請求都重新校驗賬號狀態——停用即時生效，無需等待會話過期。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


SESSION_TTL_HOURS = 12
# 已驗證 Basic 憑據的短時快取：避免每請求重算 scrypt（約 27 ms/次）。
# 只保存憑據的哈希與服務端身份，不保存口令；停用帳號時立即清空。
BASIC_CACHE_TTL_SECONDS = 300


class AuthError(Exception):
    """401：未認證或憑據無效。"""


class AccountDisabled(AuthError):
    """403：賬號已被停用。"""


class Forbidden(Exception):
    """403：已認證但角色不足（管理操作）。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """scrypt 加鹽哈希；返回 (salt_hex, hash_hex)。"""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    try:
        expected = bytes.fromhex(hash_hex)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                                n=2 ** 14, r=8, p=1, dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


@dataclass(frozen=True)
class Identity:
    """服務端身份：工作台事件的操作者以此為準，前端自填的 actor 僅作展示。"""
    account_id: str      # 用戶名（唯一，casefold）
    display_name: str
    role: str            # admin / operator / reviewer / finance / audit


class AuthStore:
    """賬戶與會話存儲（獨立 SQLite；停用即撤銷全部會話）。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._basic_cache: dict[str, tuple[Identity, float]] = {}
        self._basic_lock = threading.Lock()
        with self._connect() as connection:
            self._ensure_schema(connection)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL,
                salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                disabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0
            );
        """)

    # ---------- 賬戶管理 ----------

    def create_account(self, username: str, display_name: str, password: str,
                       role: str = "operator") -> Identity:
        account_id = username.strip().casefold()
        if not account_id or not isinstance(password, str) or len(password) < 4:
            raise ValueError("賬號不可留空，口令至少 4 個字元。")
        salt, digest = hash_password(password)
        with self._connect() as connection:
            self._ensure_schema(connection)
            connection.execute(
                "INSERT INTO accounts (id, display_name, role, salt, password_hash, disabled, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (account_id, display_name.strip() or account_id, role, salt, digest,
                 0, _now().isoformat(timespec="seconds")))
        return Identity(account_id, display_name.strip() or account_id, role)

    def set_disabled(self, username: str, *, disabled: bool = True) -> bool:
        """停用/恢復賬戶；停用即撤銷其全部會話（即時生效）。"""
        account_id = username.strip().casefold()
        with self._connect() as connection:
            self._ensure_schema(connection)
            cursor = connection.execute(
                "UPDATE accounts SET disabled=? WHERE id=?", (int(disabled), account_id))
            if disabled:
                connection.execute(
                    "UPDATE sessions SET revoked=1 WHERE account_id=?", (account_id,))
        # 停用即時生效：快取的 Basic 身份一併失效。
        with self._basic_lock:
            self._basic_cache.clear()
        return cursor.rowcount == 1

    def list_accounts(self) -> list[dict]:
        with self._connect() as connection:
            self._ensure_schema(connection)
            return [dict(row) for row in connection.execute(
                "SELECT id, display_name, role, disabled, created_at FROM accounts ORDER BY id")]

    def ensure_defaults(self, bootstrap_password: str | None = None, *, demo: bool = True) -> dict:
        """首次啟動建立賬戶。admin 口令來自環境或隨機；遠端部署（demo=False）
        絶不建立弱口令演示賬號，避免職責分離形同虛設。"""
        created: dict[str, str] = {}
        with self._connect() as connection:
            self._ensure_schema(connection)
            (count,) = connection.execute("SELECT COUNT(*) FROM accounts").fetchone()
        if count:
            return created
        admin_password = bootstrap_password or secrets.token_urlsafe(12)
        self.create_account("admin", "系統管理員", admin_password, role="admin")
        if demo:
            self.create_account("demo", "本機演示員", "demo", role="operator")
            self.create_account("reviewer_demo", "評審員（演示）", "demo", role="reviewer")
            self.create_account("approver_demo", "主管（演示）", "demo", role="approver")
            self.create_account("finance_demo", "財務（演示）", "demo", role="finance")
            created.update({"demo": "demo", "reviewer_demo": "demo",
                            "approver_demo": "demo", "finance_demo": "demo"})
        created["admin"] = admin_password
        return created

    # ---------- 認證 ----------

    def _identity(self, row: sqlite3.Row) -> Identity:
        return Identity(row["id"], row["display_name"], row["role"])

    def verify_login(self, username: str, password: str) -> Identity:
        account_id = username.strip().casefold()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        if row is None or not verify_password(password, row["salt"], row["password_hash"]):
            raise AuthError("賬號或口令無效。")
        if row["disabled"]:
            raise AccountDisabled("賬號已停用，請聯絡管理員。")
        return self._identity(row)

    def create_session(self, identity: Identity) -> str:
        token = secrets.token_urlsafe(32)
        expires = (_now() + timedelta(hours=SESSION_TTL_HOURS)).isoformat(timespec="seconds")
        with self._connect() as connection:
            self._ensure_schema(connection)
            connection.execute(
                "INSERT INTO sessions (token, account_id, created_at, expires_at, revoked)"
                " VALUES (?,?,?,?,0)",
                (token, identity.account_id, _now().isoformat(timespec="seconds"), expires))
        return token

    def identity_from_token(self, token: str) -> Identity:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
            account = (connection.execute("SELECT * FROM accounts WHERE id=?", (row["account_id"],))
                       .fetchone() if row else None)
        if row is None or row["revoked"]:
            raise AuthError("會話已失效，請重新登入。")
        if datetime.fromisoformat(row["expires_at"]) < _now():
            raise AuthError("會話已過期，請重新登入。")
        if account is None or account["disabled"]:
            raise AccountDisabled("賬號已停用，請聯絡管理員。")
        return self._identity(account)

    def identity_from_basic(self, authorization: str | None) -> Identity:
        """解析 HTTP Basic 憑據；短時快取已驗證身份，停用時由 set_disabled 清空。"""
        import base64
        if not authorization or not authorization.lower().startswith("basic "):
            raise AuthError("請先登入。")
        # 快取鍵是憑據的 SHA-256，不保存口令明文；命中後仍受停用清空與 TTL 約束。
        key = hashlib.sha256(authorization.encode("utf-8")).hexdigest()
        now = time.monotonic()
        with self._basic_lock:
            cached = self._basic_cache.get(key)
            if cached and cached[1] > now:
                return cached[0]
        try:
            decoded = base64.b64decode(authorization.split(" ", 1)[1].strip()).decode("utf-8")
            username, _, password = decoded.partition(":")
        except (ValueError, UnicodeError) as exc:
            raise AuthError("認證憑據格式無效。") from exc
        identity = self.verify_login(username, password)
        with self._basic_lock:
            self._basic_cache[key] = (identity, now + BASIC_CACHE_TTL_SECONDS)
        return identity

    def identity_from_header(self, authorization: str | None) -> Identity:
        """Bearer 會話優先，其次 Basic；無憑據即 401。"""
        if authorization and authorization.lower().startswith("bearer "):
            return self.identity_from_token(authorization.split(" ", 1)[1].strip())
        return self.identity_from_basic(authorization)
