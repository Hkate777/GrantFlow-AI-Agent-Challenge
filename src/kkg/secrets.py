"""加密密鑰環：把模型 API Key 等憑據加密存放，明文絶不落盤或進日誌。

場景16定位：機構憑據屬最高敏感等級。相較於直接讀取環境變數
（明文、易被進程清單/日誌/異常堆疊洩漏），本模組提供：

- scrypt 由主口令派生加解密金鑰（參數固定，輸出確定）；
- HMAC-SHA256 串流加密 + encrypt-then-MAC 認證，篡改即拒絶讀取；
- 每個密鑰環檔案獨立 salt，每次寫入獨立 nonce；
- 檔案權限 0600；清單只回傳鍵名與遮蔽值，絶不回傳明文；
- 僅用標準庫，無需安裝 cryptography。

限制：這是機構內部單機演示級方案；正式部署應改用硬件密鑰/HSM 或
雲端 KMS，並配合輪替與存取審計。此處不自行實現加密算法，只組合
標準庫既有原語（scrypt + HMAC），降低實現失誤風險。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any

_VERSION = 1
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DIGEST = hashlib.sha256
_KEY_LEN = 32
_ENV_PASSPHRASE = "GRANTFLOW_VAULT_PASS"
_ENV_PATH = "GRANTFLOW_VAULT_PATH"
_DEFAULT_PATH = "output/.secrets.vault.json"


class VaultError(Exception):
    """密鑰環錯誤：口令錯誤、檔案篡改或格式無效。"""


class _Crypto:
    """scrypt 派生 + HMAC 串流加密 + 認證的內部實現（僅標準庫原語）。"""

    @staticmethod
    def derive(passphrase: str, salt: bytes) -> tuple[bytes, bytes]:
        material = hashlib.scrypt(
            passphrase.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
            dklen=_KEY_LEN * 2,
        )
        return material[:_KEY_LEN], material[_KEY_LEN:]

    @staticmethod
    def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
        chunks: list[bytes] = []
        counter = 0
        produced = 0
        while produced < length:
            block = hmac.new(key, nonce + counter.to_bytes(4, "big"), _DIGEST).digest()
            chunks.append(block)
            produced += len(block)
            counter += 1
        return b"".join(chunks)[:length]

    @classmethod
    def encrypt(cls, plaintext: bytes, enc_key: bytes, mac_key: bytes, nonce: bytes) -> bytes:
        cipher = bytes(a ^ b for a, b in
                       zip(plaintext, cls._keystream(enc_key, nonce, len(plaintext))))
        tag = hmac.new(mac_key, nonce + cipher, _DIGEST).digest()
        return nonce + tag + cipher

    @classmethod
    def decrypt(cls, blob: bytes, enc_key: bytes, mac_key: bytes) -> bytes:
        if len(blob) < 28:
            raise VaultError("密鑰環內容損毀。")
        nonce, tag, cipher = blob[:12], blob[12:44], blob[44:]
        expected = hmac.new(mac_key, nonce + cipher, _DIGEST).digest()
        if not hmac.compare_digest(tag, expected):
            raise VaultError("密鑰環認證失敗：口令錯誤或檔案已被篡改。")
        return bytes(a ^ b for a, b in
                     zip(cipher, cls._keystream(enc_key, nonce, len(cipher))))


class SecretVault:
    """加密密鑰環：set/get/delete/list，明文只在記憶體短暫存在。"""

    def __init__(self, path: str | Path | None = None, passphrase: str | None = None) -> None:
        self.path = Path(path or os.environ.get(_ENV_PATH) or _DEFAULT_PATH)
        self._passphrase = passphrase or os.environ.get(_ENV_PASSPHRASE, "")
        if not self._passphrase:
            raise VaultError("未設定主口令：請設定環境變數 GRANTFLOW_VAULT_PASS。")

    def exists(self) -> bool:
        return self.path.is_file()

    def _load(self) -> tuple[dict, bytes, bytes]:
        # 首次建立：檔案尚未存在，視為空環並產生新 salt
        if not self.exists():
            return {}, secrets.token_bytes(16), b""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if raw.get("version") != _VERSION:
                raise VaultError("密鑰環版本不相容。")
            salt = bytes.fromhex(raw["salt"])
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise VaultError("密鑰環格式無效或無法讀取。") from exc
        enc_key, mac_key = _Crypto.derive(self._passphrase, salt)
        try:
            blob = bytes.fromhex(raw["blob"])
            plain = _Crypto.decrypt(blob, enc_key, mac_key)
            data = json.loads(plain)
        except VaultError:
            raise
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise VaultError("密鑰環內容無法解碼。") from exc
        if not isinstance(data, dict):
            raise VaultError("密鑰環內容格式錯誤。")
        return data, salt, mac_key

    def _save(self, data: dict, salt: bytes) -> None:
        enc_key, mac_key = _Crypto.derive(self._passphrase, salt)
        plain = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        blob = _Crypto.encrypt(plain, enc_key, mac_key, secrets.token_bytes(12))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "version": _VERSION, "salt": salt.hex(), "blob": blob.hex(),
        }), encoding="utf-8")
        # 限制檔案權限：僅擁有者可讀寫（Windows 忽略 chmod，不影響）
        try:
            self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def get(self, name: str) -> str | None:
        data, _, _ = self._load()
        return data.get(name)

    def set(self, name: str, value: str) -> None:
        if not name or not isinstance(name, str) or "\n" in name:
            raise VaultError("密鑰名稱無效。")
        data, salt, _ = self._load()
        data[name] = value
        self._save(data, salt)

    def delete(self, name: str) -> bool:
        data, salt, _ = self._load()
        if name not in data:
            return False
        del data[name]
        self._save(data, salt)
        return True

    def names(self) -> list[str]:
        data, _, _ = self._load()
        return sorted(data)

    @staticmethod
    def mask(value: str) -> str:
        """遮蔽顯示：僅留前 4 與後 2 碼，其餘以星號取代。"""
        if not value:
            return "（空）"
        if len(value) <= 6:
            return value[0] + "*" * (len(value) - 1)
        return f"{value[:4]}{'*' * max(0, len(value) - 6)}{value[-2:]}"

    def listing(self) -> list[dict[str, str]]:
        """供 UI/CLI 顯示：鍵名 + 遮蔽值，絶不回傳明文。"""
        data, _, _ = self._load()
        return [{"name": name, "value": self.mask(data[name])} for name in sorted(data)]

    def export_env(self) -> dict[str, str]:
        """解密全部憑據為 dict，僅供同進程內注入 LLM 客戶端；不可列印。"""
        data, _, _ = self._load()
        return dict(data)

    def verify(self) -> bool:
        """驗證口令與檔案完整性；成功回傳 True，否則拋 VaultError。"""
        self._load()
        return True


def apply_vault_to_env(vault: SecretVault | None = None) -> list[str]:
    """把密鑰環中的 Azure/OpenAI/Anthropic/Gemini 憑據注入當前進程環境。

    只補足尚未設定的環境變數（環境既有值優先），且不回傳任何明文；
    回傳已注入的變數名稱清單供審計記録。
    """
    if vault is None:
        try:
            vault = SecretVault()
        except VaultError:
            return []
    if not vault.exists():
        return []
    try:
        secrets_map = vault.export_env()
    except VaultError:
        return []
    injected: list[str] = []
    for name, value in secrets_map.items():
        if os.environ.get(name):
            continue
        os.environ[name] = value
        injected.append(name)
    return injected


_VAULT_KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
               "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL")


def vault_example() -> dict[str, Any]:
    """回傳可用於 CLI 提示的說明（不含任何真實憑據）。"""
    return {
        "env_passphrase": _ENV_PASSPHRASE,
        "env_path": _ENV_PATH,
        "default_path": _DEFAULT_PATH,
        "supported_keys": list(_VAULT_KEYS),
    }
