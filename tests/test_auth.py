"""P0-06 回歸：機構登入、服務端操作者、賬戶停用即時生效、導出需認證。"""
import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app


def _client(path, auth=("demo", "demo")):
    client = TestClient(app.create_app(path), client=("127.0.0.1", 41000))
    if auth:
        token = base64.b64encode(":".join(auth).encode()).decode()
        client.headers["Authorization"] = f"Basic {token}"
    return client


def _metadata(app_id="AUTH-1"):
    return {"app_id": app_id, "program_id": "HK-ACC-2027", "title": "Fixture project",
            "applicant": "Fixture team", "email": "test@example.test", "team": ["Team lead"],
            "fields": {"org_type": "university", "contact": "Team lead", "email": "test@example.test",
                       "budget": 120000, "duration_m": 6}}


def _files():
    return [("files", ("proposal.txt", b"requested_budget: 120000 HKD", "text/plain")),
            ("files", ("budget.csv", b"item,amount\nMaterials,120000\n", "text/csv")),
            ("files", ("cv.txt", b"Team lead biography.", "text/plain"))]


def _basic(user, password):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_api_requires_credentials_without_any_header(tmp_path):
    client = _client(tmp_path / "cases.sqlite3", auth=None)
    assert client.get("/api/cases").status_code == 401
    assert client.get("/api/cases").headers["www-authenticate"].startswith("Basic")
    # 登錄頁與靜態資源公開，供瀏覽器進入登入流程。
    assert client.get("/").status_code == 200


def test_wrong_password_rejected_correct_account_accepted(tmp_path):
    database = tmp_path / "cases.sqlite3"
    bad = _client(database, auth=("demo", "wrong-password"))
    assert bad.get("/api/cases").status_code == 401
    ok = _client(database)
    assert ok.get("/api/cases").status_code == 200


def test_login_endpoint_returns_bearer_session(tmp_path):
    client = _client(tmp_path / "cases.sqlite3", auth=None)
    response = client.post("/api/auth/login",
                           json={"username": "demo", "password": "demo"})
    assert response.status_code == 200
    token = response.json()["token"]

    bearer = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/auth/me", headers=bearer)
    assert me.status_code == 200
    assert me.json()["account_id"] == "demo"

    # Bearer 會話可用於業務 API，亦受停用/撤銷約束（下一測試）。
    assert client.get("/api/cases", headers=bearer).status_code == 200


def test_disabled_account_is_rejected_immediately(tmp_path):
    client = _client(tmp_path / "cases.sqlite3")
    login = client.post("/api/auth/login", json={"username": "demo", "password": "demo"})
    assert login.status_code == 200
    bearer = {"Authorization": f"Bearer {login.json()['token']}"}
    assert client.get("/api/auth/me", headers=bearer).json()["account_id"] == "demo"

    # 停用 demo 賬號（經 AuthStore 直接管，等價管理員操作）——Bearer 與 Basic 均即時失效。
    auth_store = client.app.state.auth
    auth_store.set_disabled("demo", disabled=True)

    assert client.get("/api/auth/me", headers=bearer).status_code == 401
    basic = _client(tmp_path / "cases.sqlite3", auth=("demo", "demo"))
    assert basic.get("/api/cases").status_code == 403   # 停用 → 403（非 401）

    # 恢復後可再次訪問。
    auth_store.set_disabled("demo", disabled=False)
    assert basic.get("/api/cases").status_code == 200


def test_audit_events_bind_server_identity_not_client_actor(tmp_path):
    client = _client(tmp_path / "cases.sqlite3")
    response = client.post("/api/cases", data={"metadata": json.dumps(_metadata())},
                           files=[("files", ("proposal.txt", b"requested_budget: 120000 HKD",
                                             "text/plain")),
                                  ("files", ("budget.csv", b"item,amount\nMaterials,120000\n",
                                             "text/csv")),
                                  ("files", ("cv.txt", b"Team lead biography.", "text/plain"))])
    case = response.json()
    criteria = {item["name"]: item["max_score"] for item in
                next(program["criteria"] for program in client.get("/api/programs").json()
                     if program["program_id"] == case["program_id"])}
    scored = client.put(f"/api/cases/{case['id']}/scores", json={
        "revision": case["revision"],
        "scores": [{"reviewer": "Reviewer A", "criteria": criteria, "comment": "ok."}],
        "actor": "前端偽造身份",   # P0-06：此值必須被忽略
    }).json()
    # 事件的操作者是服務端身份（demo 賬號的顯示名），而非請求體自填值。
    assert scored["audit"][-1]["actor"] == "本機演示員"
    assert scored["audit"][-1]["actor"] != "前端偽造身份"
    # me 端點確認服務端身份。
    assert client.get("/api/auth/me").json()["account_id"] == "demo"


def test_downloads_require_authentication(tmp_path):
    client = _client(tmp_path / "cases.sqlite3")
    response = client.post("/api/cases", data={"metadata": json.dumps(_metadata())},
                           files=[("files", ("proposal.txt", b"requested_budget: 120000 HKD",
                                             "text/plain")),
                                  ("files", ("budget.csv", b"item,amount\nMaterials,120000\n",
                                             "text/csv")),
                                  ("files", ("cv.txt", b"Team lead biography.", "text/plain"))])
    case = response.json()
    anonymous = TestClient(app.create_app(tmp_path / "cases.sqlite3"),
                           client=("127.0.0.1", 41000))
    assert anonymous.get(f"/api/cases/{case['id']}/pack").status_code == 401
    assert anonymous.get(f"/api/cases/{case['id']}/report").status_code == 401
    assert client.get(f"/api/cases/{case['id']}/pack").status_code == 200


def test_non_admin_cannot_disable_accounts(tmp_path):
    client = _client(tmp_path / "cases.sqlite3")
    response = client.post("/api/auth/disable", json={"username": "admin"})
    assert response.status_code == 403
