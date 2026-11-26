"""個案級授權骨架回歸：展示可見性判定、授權表與 API 接線的行為。

這是 P0-02/03 的代碼側骨架測試，驗證「角色之外還能限制看哪幾個案子」。
"""
from __future__ import annotations

import base64
import json

from fastapi.testclient import TestClient

import app as app_module
from kkg.access import can_view_case, normalize_grants
from kkg.auth import Identity
from kkg.serve import RuntimeConfig


def _headers(user: str, password: str = "demo") -> dict:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _reviewer(id_: str = "rev_a", role: str = "reviewer") -> Identity:
    return Identity(id_, id_, role)


def _case(case_id: str, assignments=None) -> dict:
    return {"id": case_id, "assignments": assignments or []}


# ---------- 純函數：can_view_case ----------

def test_admin_always_sees_all():
    assert can_view_case(_case("c1"), Identity("boss", "boss", "admin")) is True
    assert can_view_case(_case("c1"), Identity("boss", "boss", "admin"), {}) is True


def test_assignment_mode_reviewer_only_own_cases():
    case = _case("c1", [{"reviewer_id": "rev_a"}, {"reviewer_id": "rev_b"}])
    assert can_view_case(case, _reviewer("rev_a")) is True
    assert can_view_case(case, _reviewer("rev_b")) is True
    assert can_view_case(case, _reviewer("rev_c")) is False


def test_assignment_mode_other_roles_full_and_legacy_cases():
    case = _case("c1", [{"reviewer_id": "rev_a"}])
    assert can_view_case(case, Identity("op", "op", "operator")) is True
    assert can_view_case(case, Identity("fin", "fin", "finance")) is True
    # 舊個案沒有 assignments：維持舊行為，所有角色可見。
    assert can_view_case(_case("legacy"), _reviewer("rev_c")) is True


def test_grant_mode_whitelist_overrides_assignment():
    case = _case("c1", [{"reviewer_id": "rev_a"}])
    grants = {"rev_a": {"c1"}, "op": set()}
    # rev_a 在白名單且列出 c1 → 可見；即使被指派也一樣。
    assert can_view_case(case, _reviewer("rev_a"), grants) is True
    # rev_b 未在白名單 → 不可見，即使被指派。
    assert can_view_case(case, _reviewer("rev_b"), grants) is False
    # operator 在白名單但沒列 c1 → 不可見。
    assert can_view_case(case, Identity("op", "op", "operator"), grants) is False
    # 管理員不受白名單約束。
    assert can_view_case(case, Identity("boss", "boss", "admin"), grants) is True


def test_normalize_grants_accepts_both_shapes_and_ignores_junk():
    raw = {"Rev_A": ["c1", "c2"], "op": {"cases": ["c2"]}, "bad": "not-a-list", "": ["c3"]}
    normalized = normalize_grants(raw)
    assert normalized == {"rev_a": {"c1", "c2"}, "op": {"c2"}}
    assert normalize_grants(None) == {}


# ---------- API 接線 ----------

def _case_payload(app_id: str) -> dict:
    return {"app_id": app_id, "program_id": "HK-ACC-2027", "title": "T", "applicant": "A",
            "email": "a@b.test", "team": [],
            "fields": {"org_type": "university", "budget": 120000, "duration_m": 6}}


def _client(tmp_path, *, case_scope: bool):
    runtime = RuntimeConfig(host="127.0.0.1", allowed_hosts=("testserver",), case_scope=case_scope)
    return TestClient(app_module.create_app(str(tmp_path / "cases.sqlite3"), runtime=runtime),
                      client=("127.0.0.1", 41000))


def _submit(client, app_id: str = "SCOPE-1") -> dict:
    response = client.post("/api/cases", data={"metadata": json.dumps(_case_payload(app_id))},
                           files=[("files", ("p.txt", b"x", "text/plain"))],
                           headers=_headers("demo"))
    assert response.status_code == 201, response.text
    return response.json()


def test_scope_disabled_keeps_legacy_full_visibility(tmp_path):
    """開關關閉（預設）時，行為與舊版完全一致。"""
    client = _client(tmp_path, case_scope=False)
    case = _submit(client)
    # 評委未獲指派仍可讀（舊行為）。
    assert client.get(f"/api/cases/{case['id']}", headers=_headers("reviewer_demo")).status_code == 200
    assert len(client.get("/api/cases", headers=_headers("reviewer_demo")).json()) == 1


def test_scope_enabled_assignment_mode_hides_other_reviewers_case(tmp_path):
    """啟用指派模式：未獲指派的評委看不到個案（列表與詳情皆然）。"""
    client = _client(tmp_path, case_scope=True)
    case = _submit(client)
    # 指派 reviewer_demo。
    assigned = client.post(f"/api/cases/{case['id']}/assignments",
                           json={"reviewer_id": "reviewer_demo", "revision": case["revision"]},
                           headers=_headers("demo"))
    assert assigned.status_code == 200, assigned.text
    # reviewer_demo 被指派 → 可讀；reviewer2_demo 未被指派 → 404，列表為空。
    assert client.get(f"/api/cases/{case['id']}", headers=_headers("reviewer_demo")).status_code == 200
    unseen = client.get(f"/api/cases/{case['id']}", headers=_headers("reviewer2_demo"))
    assert unseen.status_code == 404
    assert client.get("/api/cases", headers=_headers("reviewer2_demo")).json() == []


def test_scope_enabled_grant_mode_limits_operator(tmp_path):
    """授權表模式可限制 operator 只看指定個案（真正的資料級授權）。"""
    client = _client(tmp_path, case_scope=True)
    c1 = _submit(client, "SCOPE-A")
    c2 = _submit(client, "SCOPE-B")
    admin = _headers("test-admin", "test-pass")
    client.app.state.auth.create_account("test-admin", "測試管理員", "test-pass", role="admin")
    # 只授權 demo 看 c1。
    resp = client.put("/api/access/grants", json={"grants": {"demo": [c1["id"]]}}, headers=admin)
    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] is True
    assert client.get(f"/api/cases/{c1['id']}", headers=_headers("demo")).status_code == 200
    assert client.get(f"/api/cases/{c2['id']}", headers=_headers("demo")).status_code == 404
    listed = client.get("/api/cases", headers=_headers("demo")).json()
    assert [item["id"] for item in listed] == [c1["id"]]
    # 管理員不受限。
    assert client.get(f"/api/cases/{c2['id']}", headers=admin).status_code == 200


def test_grants_endpoint_requires_admin(tmp_path):
    client = _client(tmp_path, case_scope=True)
    assert client.get("/api/access/grants", headers=_headers("demo")).status_code == 403
    assert client.put("/api/access/grants", json={"grants": {}}, headers=_headers("demo")).status_code == 403
