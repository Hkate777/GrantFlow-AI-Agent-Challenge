"""部署與網絡防護回歸：環境配置、限流、封鎖、來源約束與健康探測。"""
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app
from kkg.serve import (IPReputation, RateLimiter, RuntimeConfig,
                       apply_security_headers, client_ip, security_event)


def test_runtime_config_from_env(monkeypatch):
    values = {
        "GRANTFLOW_HOST": "0.0.0.0",
        "GRANTFLOW_PORT": "9000",
        "GRANTFLOW_ALLOWED_HOSTS": "example.edu.hk, localhost",
        "GRANTFLOW_TRUST_PROXY": "true",
        "GRANTFLOW_RATE_LIMIT_PER_MIN": "25",
        "GRANTFLOW_AUTH_FAIL_LIMIT": "3",
        "GRANTFLOW_BAN_MINUTES": "2",
        "GRANTFLOW_ALLOWED_CIDRS": "192.0.2.0/24",
        "GRANTFLOW_PUBLIC_ORIGINS": "https://example.edu.hk",
        "GRANTFLOW_HTTPS_ONLY": "true",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    config = RuntimeConfig.from_env()
    assert config.host == "0.0.0.0"
    assert config.port == 9000
    assert config.allowed_hosts == ("example.edu.hk", "localhost")
    assert config.trust_proxy is True
    assert config.rate_per_minute == 25
    assert config.auth_fail_limit == 3
    assert config.ban_minutes == 2
    assert config.allowed_networks == ("192.0.2.0/24",)
    assert config.public_origins == ("https://example.edu.hk",)
    assert config.https_only is True
    assert config.remote_access is True
    assert config.network_allowed("192.0.2.10") is True
    assert config.network_allowed("198.51.100.10") is False


def test_rate_limiter_sliding_window():
    limiter = RateLimiter(2, window=60)
    assert limiter.allow("client-a")[0] is True
    assert limiter.allow("client-a")[0] is True
    allowed, retry = limiter.allow("client-a")
    assert allowed is False and retry >= 1
    assert limiter.allow("client-b")[0] is True


def test_ip_reputation_ban_and_clear():
    reputation = IPReputation(2, 1)
    assert reputation.banned("203.0.113.5")[0] is False
    reputation.record_failure("203.0.113.5")
    reputation.record_failure("203.0.113.5")
    assert reputation.banned("203.0.113.5")[0] is True
    reputation.record_success("203.0.113.5")
    assert reputation.banned("203.0.113.5")[0] is True
    reputation._bans["203.0.113.5"] = 0  # 模擬封鎖期滿
    assert reputation.banned("203.0.113.5")[0] is False


def test_client_ip_only_trusts_forwarded_header_when_configured():
    request = SimpleNamespace(
        headers={"x-forwarded-for": "198.51.100.1, 203.0.113.9"},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    assert client_ip(request) == "127.0.0.1"
    assert client_ip(request, trust_proxy=True) == "203.0.113.9"


def test_security_headers_and_log_redaction(tmp_path):
    response = SimpleNamespace(headers={})
    apply_security_headers(response, RuntimeConfig(https_only=True))
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Content-Security-Policy"].startswith("default-src 'self'")
    assert response.headers["Strict-Transport-Security"].startswith("max-age=")
    security_event(tmp_path, "auth-failure", "192.0.2.1", "/api/cases",
                   "status=401 password=secret token=abc")
    text = (tmp_path / "security.log").read_text(encoding="utf-8")
    assert "auth-failure" in text
    assert "password=secret" not in text
    assert "token=abc" not in text


def test_health_endpoints_are_public_and_hardened(tmp_path):
    client = TestClient(app.create_app(tmp_path / "cases.sqlite3"),
                        client=("127.0.0.1", 41000))
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/readyz").json()["status"] == "ready"
    assert client.get("/healthz").headers["x-frame-options"] == "DENY"


def test_remote_mode_allows_non_loopback_but_requires_auth(tmp_path):
    config = RuntimeConfig(host="0.0.0.0", allowed_hosts=("testserver",))
    client = TestClient(app.create_app(tmp_path / "cases.sqlite3", runtime=config),
                        client=("198.51.100.7", 41000))
    # 遠端連線不再被網路閘擋（403），而是正常要求認證（401）。
    assert client.get("/api/cases").status_code == 401


def test_cidr_allowlist_blocks_unlisted_client(tmp_path):
    config = RuntimeConfig(host="0.0.0.0", allowed_hosts=("testserver",),
                           allowed_networks=("192.0.2.0/24",))
    client = TestClient(app.create_app(tmp_path / "cases.sqlite3", runtime=config),
                        client=("198.51.100.7", 41000))
    assert client.get("/api/cases").status_code == 403


def test_rate_limit_returns_429_and_healthz_is_exempt(tmp_path):
    config = RuntimeConfig(host="127.0.0.1", rate_per_minute=2)
    client = TestClient(app.create_app(tmp_path / "cases.sqlite3", runtime=config),
                        client=("127.0.0.1", 41000))
    assert client.get("/healthz").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/").status_code == 200
    third = client.get("/")
    assert third.status_code == 429
    assert third.headers["retry-after"].isdecimal()


def test_foreign_origin_rejected_for_writes(tmp_path):
    config = RuntimeConfig(host="0.0.0.0", allowed_hosts=("testserver",))
    client = TestClient(app.create_app(tmp_path / "cases.sqlite3", runtime=config),
                        client=("198.51.100.7", 41000))
    response = client.post("/api/auth/login",
                           json={"username": "demo", "password": "demo"},
                           headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_reverse_proxy_origin_matches_public_host(tmp_path):
    config = RuntimeConfig(host="0.0.0.0", allowed_hosts=("grantflow.example.edu.hk",),
                           trust_proxy=True)
    client = TestClient(app.create_app(tmp_path / "cases.sqlite3", runtime=config),
                        client=("127.0.0.1", 41000),
                        base_url="http://grantflow.example.edu.hk")
    headers = {
        "Origin": "https://grantflow.example.edu.hk",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "grantflow.example.edu.hk",
        "X-Forwarded-For": "198.51.100.7",
    }
    response = client.post("/api/auth/login",
                           json={"username": "demo", "password": "demo"},
                           headers=headers)
    assert response.status_code == 200
