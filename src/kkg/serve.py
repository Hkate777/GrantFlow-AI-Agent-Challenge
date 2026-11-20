"""部署與網絡防護的運行時設定。

設計目標：同一份程式在本機只聽 loopback，在容器或雲端可由環境變數切換為
可接受遠端連線，同時保留速率限制、來源約束、失敗封鎖與安全響應頭。
本層不依賴 FastAPI，方便單獨測試；app.py 負責把它接進中間件。
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip().lstrip("-").isdecimal():
        return default
    return max(minimum, min(maximum, int(raw.strip())))


def _env_list(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if raw is None:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class RuntimeConfig:
    """由環境變數決定的部署參數；所有預設值維持本機演示的安全邊界。"""

    host: str = "127.0.0.1"
    port: int = 8000
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "testserver")
    trust_proxy: bool = False
    rate_per_minute: int = 120
    auth_fail_limit: int = 10
    ban_minutes: int = 15
    allowed_networks: tuple[str, ...] = ()
    public_origins: tuple[str, ...] = ()
    https_only: bool = False

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        hosts = _env_list("GRANTFLOW_ALLOWED_HOSTS") or cls.allowed_hosts
        networks = tuple(str(ipaddress.ip_network(item, strict=False))
                         for item in _env_list("GRANTFLOW_ALLOWED_CIDRS"))
        return cls(
            host=os.environ.get("GRANTFLOW_HOST", cls.host).strip() or cls.host,
            port=_env_int("GRANTFLOW_PORT", cls.port, 1, 65535),
            allowed_hosts=hosts,
            trust_proxy=_env_bool("GRANTFLOW_TRUST_PROXY"),
            rate_per_minute=_env_int("GRANTFLOW_RATE_LIMIT_PER_MIN", cls.rate_per_minute, 1, 100_000),
            auth_fail_limit=_env_int("GRANTFLOW_AUTH_FAIL_LIMIT", cls.auth_fail_limit, 1, 100_000),
            ban_minutes=_env_int("GRANTFLOW_BAN_MINUTES", cls.ban_minutes, 1, 1440),
            allowed_networks=networks,
            public_origins=_env_list("GRANTFLOW_PUBLIC_ORIGINS"),
            https_only=_env_bool("GRANTFLOW_HTTPS_ONLY"),
        )

    @property
    def remote_access(self) -> bool:
        """管理員把監聽地址設為非 loopback，即明確要求接受遠端連線。"""
        try:
            return not ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            return True

    def network_allowed(self, address: str) -> bool:
        if not self.allowed_networks:
            return True
        try:
            peer = ipaddress.ip_address(address)
        except ValueError:
            return False
        return any(peer in ipaddress.ip_network(item) for item in self.allowed_networks)


def client_ip(request, *, trust_proxy: bool = False) -> str:
    """取得真實客戶端地址。

    只在信任反向代理時讀 X-Forwarded-For，並取最右值（由最後一跳代理追加，
    客戶端較難偽造）；直接對外服務時一律使用 TCP 對端地址。
    """
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        candidates = [item.strip() for item in forwarded.split(",") if item.strip()]
        if candidates:
            return candidates[-1]
        real_ip = request.headers.get("x-real-ip", "").strip()
        if real_ip:
            return real_ip
    return request.client.host if request.client else "unknown"


class RateLimiter:
    """以單調時鐘實作的每客戶端滑動窗口限流，執行緒安全。"""

    def __init__(self, limit: int, window: int = 60):
        self.limit = max(1, limit)
        self.window = max(1, window)
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._last_prune = time.monotonic()

    def allow(self, key: str) -> tuple[bool, int]:
        """回傳 (是否放行, 重試等待秒數)。"""
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            if now - self._last_prune > self.window * 5:
                self._prune(cutoff)
                self._last_prune = now
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                return False, max(1, int(cutoff + self.window - now) + 1)
            hits.append(now)
            return True, 0

    def _prune(self, cutoff: float) -> None:
        for key in list(self._hits):
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if not hits:
                del self._hits[key]


class IPReputation:
    """短期失敗封鎖：連續認證失敗達閾值即臨時拒絶該地址。"""

    def __init__(self, fail_limit: int, ban_minutes: int):
        self.fail_limit = max(1, fail_limit)
        self.ban_seconds = max(1, ban_minutes) * 60
        self._fails: dict[str, tuple[int, float]] = {}
        self._bans: dict[str, float] = {}
        self._lock = threading.Lock()

    def banned(self, address: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            until = self._bans.get(address)
            if until is None:
                return False, 0
            if until <= now:
                del self._bans[address]
                self._fails.pop(address, None)
                return False, 0
            return True, max(1, int(until - now) + 1)

    def record_failure(self, address: str) -> int:
        now = time.monotonic()
        with self._lock:
            count, first = self._fails.get(address, (0, now))
            if now - first > self.ban_seconds:
                count, first = 0, now
            count += 1
            self._fails[address] = (count, first)
            if count >= self.fail_limit:
                self._bans[address] = now + self.ban_seconds
                self._fails.pop(address, None)
            return count

    def record_success(self, address: str) -> None:
        with self._lock:
            self._fails.pop(address, None)


def apply_security_headers(response, config: RuntimeConfig) -> None:
    """為所有響應加上縱深防禦響應頭，不影響業務資料。"""
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    if config.https_only:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"


_LOG_LOCK = threading.Lock()
_SECRET_PATTERN = re.compile(r"(?i)(password|token|authorization|api[_-]?key)\s*=\s*\S+")


def security_event(directory: Path, kind: str, address: str, path: str, detail: str = "") -> None:
    """寫入結構化安全日誌；絶不記錄請求內容、口令或授權頭。"""
    record = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": kind,
        "ip": address,
        "path": path[:300],
    }
    if detail:
        record["detail"] = _SECRET_PATTERN.sub(r"\1=<redacted>", detail)[:200]
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with _LOG_LOCK, (directory / "security.log").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass  # 日誌失敗不中斷服務，但上層可另行監控檔案。
