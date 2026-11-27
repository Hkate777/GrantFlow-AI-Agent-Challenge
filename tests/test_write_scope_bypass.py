"""個案級授權寫穿回歸：讀取的可見性閘門必須同時約束所有寫端點。

此前 ``workspace._edit()`` 直接 ``store.get(case_id)`` 不帶 viewer/grants，
寫端點亦未接 ``_scope_args()``，令「已指派但白名單不給看」的評委雖然讀個案
得 404，卻仍能申報衝突與提交評分。本檔以真實 HTTP 端到端重現並堵塞該路徑，
同時確認正常路徑（可見且獲指派）未被過度收緊。
"""
import base64
import json

from fastapi.testclient import TestClient

import app
from conftest import make_admin
from kkg.serve import RuntimeConfig
from test_assignments import _criteria, _files, _metadata


def _headers(user, password="demo") -> dict:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _client(tmp_path) -> TestClient:
    runtime = RuntimeConfig(host="127.0.0.1", allowed_hosts=("testserver",), case_scope=True)
    client = TestClient(app.create_app(str(tmp_path / "cases.sqlite3"), runtime=runtime),
                        client=("127.0.0.1", 41000))
    client.headers.update(_headers("demo"))
    return client


def _submit(client: TestClient, app_id: str) -> dict:
    response = client.post("/api/cases", data={"metadata": json.dumps(_metadata(app_id))},
                           files=_files())
    assert response.status_code == 201, response.text
    return response.json()


def _grant(client: TestClient, admin: dict, grants: dict) -> None:
    response = client.put("/api/access/grants", json={"grants": grants}, headers=admin)
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is True


def test_assigned_but_unlisted_reviewer_cannot_write_hidden_case(tmp_path):
    """PoC：白名單只給評委看 A；經辦把 B 指派給評委；讀 B 被擋，寫 B 亦須被擋。"""
    client = _client(tmp_path)
    c1 = _submit(client, "WSCOPE-A")
    c2 = _submit(client, "WSCOPE-B")
    admin = make_admin(client)
    # 經辦可見 A、B；reviewer_demo 只可見 A。兩張表彼此獨立。
    _grant(client, admin, {"demo": [c1["id"], c2["id"]], "reviewer_demo": [c1["id"]]})
    # 經辦把 B 指派給 reviewer_demo，製造「已指派但白名單不給看」的現實狀態。
    assigned = client.post(f"/api/cases/{c2['id']}/assignments",
                           json={"reviewer_id": "reviewer_demo", "revision": c2["revision"]})
    assert assigned.status_code == 200, assigned.text
    revision = assigned.json()["revision"]

    # 讀 B → 404（原本已擋住）。
    assert client.get(f"/api/cases/{c2['id']}", headers=_headers("reviewer_demo")).status_code == 404
    # 寫 B → 從前 200 寫穿，現在必須與讀路徑一致回 404。
    conflict = client.post(f"/api/cases/{c2['id']}/conflict",
                           json={"revision": revision, "has_conflict": False},
                           headers=_headers("reviewer_demo"))
    assert conflict.status_code == 404, conflict.text
    score = client.post(f"/api/cases/{c2['id']}/scores",
                        json={"revision": revision, "criteria": _criteria(client, c2)},
                        headers=_headers("reviewer_demo"))
    assert score.status_code == 404, score.text
    # 由管理員讀回確認未被寫穿：評分仍空、指派仍停留在 assigned。
    after = client.get(f"/api/cases/{c2['id']}", headers=admin).json()
    assert after["scores"] == []
    assert after["assignments"][0]["status"] == "assigned"


def test_every_write_endpoint_honours_case_visibility(tmp_path):
    """空白單（除管理員外無人可見）時，全部寫端點一律回 404。"""
    client = _client(tmp_path)
    case = _submit(client, "WSCOPE-ALL")
    admin = make_admin(client)
    _grant(client, admin, {})
    detail = client.get(f"/api/cases/{case['id']}", headers=admin).json()
    revision = detail["revision"]
    crit = _criteria(client, case)
    base = f"/api/cases/{case['id']}"

    # (方法, 後綴, JSON body, 身份)：身份角色正確但個案不可見。
    checks = [
        ("put", "/scores", {"revision": revision,
                            "scores": [{"reviewer": "X", "criteria": crit}]},
         _headers("reviewer_demo")),
        ("post", "/scores", {"revision": revision, "criteria": crit},
         _headers("reviewer_demo")),
        ("post", "/assignments", {"revision": revision, "reviewer_id": "reviewer_demo"},
         _headers("demo")),
        ("post", "/conflict", {"revision": revision, "has_conflict": False},
         _headers("reviewer_demo")),
        ("post", "/resolution", {"revision": revision, "actor": "經辦", "reason": "r"},
         _headers("demo")),
        ("post", "/decision", {"revision": revision, "actor": "主管", "reason": "r",
                               "outcome": "approved"}, _headers("approver_demo")),
        ("put", "/project", {"revision": revision, "actor": "財務", "milestones": [],
                             "periodic_reports": [], "disbursements": []},
         _headers("finance_demo")),
    ]
    for method, suffix, body, headers in checks:
        response = getattr(client, method)(base + suffix, json=body, headers=headers)
        assert response.status_code == 404, f"{method.upper()} {suffix}: {response.text}"

    # amend 用 multipart，單獨驗證。
    amended = client.put(
        base + "/materials",
        data={"metadata": json.dumps(_metadata("WSCOPE-ALL")), "revision": str(revision)},
        files=[("files", ("p.txt", b"x", "text/plain"))],
        headers=_headers("demo"))
    assert amended.status_code == 404, amended.text

    # 全部寫都被擋：版本與內容不變。
    after = client.get(base, headers=admin).json()
    assert after["revision"] == revision
    assert after["scores"] == []
    assert after["assignments"] == []


def test_visible_and_assigned_reviewer_can_still_write(tmp_path):
    """白名單放行且獲指派：評審的正常申報與評分路徑不受影響。"""
    client = _client(tmp_path)
    case = _submit(client, "WSCOPE-OK")
    admin = make_admin(client)
    _grant(client, admin, {"demo": [case["id"]], "reviewer_demo": [case["id"]]})
    assigned = client.post(f"/api/cases/{case['id']}/assignments",
                           json={"reviewer_id": "reviewer_demo", "revision": case["revision"]})
    assert assigned.status_code == 200, assigned.text
    conflict = client.post(f"/api/cases/{case['id']}/conflict",
                           json={"revision": assigned.json()["revision"], "has_conflict": False},
                           headers=_headers("reviewer_demo"))
    assert conflict.status_code == 200, conflict.text
    score = client.post(f"/api/cases/{case['id']}/scores",
                        json={"revision": conflict.json()["revision"],
                              "criteria": _criteria(client, case)},
                        headers=_headers("reviewer_demo"))
    assert score.status_code == 200, score.text


def test_visible_but_unassigned_reviewer_still_cannot_write(tmp_path):
    """白名單放行但未獲指派：可讀而不可寫，既有「須被指派」閘門保留。"""
    client = _client(tmp_path)
    case = _submit(client, "WSCOPE-UNASSIGNED")
    admin = make_admin(client)
    _grant(client, admin, {"reviewer_demo": [case["id"]]})
    # 白名單放行 → 可讀。
    assert client.get(f"/api/cases/{case['id']}",
                      headers=_headers("reviewer_demo")).status_code == 200
    # 未獲指派 → 申報仍被既有工作流擋住（422），不是放行。
    conflict = client.post(f"/api/cases/{case['id']}/conflict",
                           json={"revision": case["revision"], "has_conflict": False},
                           headers=_headers("reviewer_demo"))
    assert conflict.status_code == 422, conflict.text
