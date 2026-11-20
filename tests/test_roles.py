"""P0-07 角色矩陣：管理員/經辦/評委/主管/財務的正負用例。

矩陣（admin 恆可執行一切）：
  收件/演示/補件/複議記錄 = operator
  評分                     = reviewer
  簽發                     = approver
  台帳                     = finance
  賬戶管理                 = admin
"""
import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app
from conftest import ROLE_AUTH


DEMO_ACCOUNTS = {
    "operator": ("demo", "demo"),
    "reviewer": ("reviewer_demo", "demo"),
    "approver": ("approver_demo", "demo"),
    "finance": ("finance_demo", "demo"),
    "admin": ("test-admin", "test-pass"),  # 各測試經 AuthStore 建立後可用（口令已知）
}


def _client(path, role="operator"):
    client = TestClient(app.create_app(path), client=("127.0.0.1", 41000))
    if role == "admin":
        existing = {account["id"] for account in client.app.state.auth.list_accounts()}
        if "test-admin" not in existing:
            client.app.state.auth.create_account("test-admin", "測試管理員", "test-pass", role="admin")
    user, password = DEMO_ACCOUNTS[role]
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    client.headers["Authorization"] = f"Basic {token}"
    return client


def _latest_revision(client, case_id) -> int:
    return client.get(f"/api/cases/{case_id}").json()["revision"]


def _metadata(app_id):
    return {"app_id": app_id, "program_id": "HK-ACC-2027", "title": "Fixture project",
            "applicant": "Fixture team", "email": "test@example.test", "team": ["Team lead"],
            "fields": {"org_type": "university", "contact": "Team lead", "email": "test@example.test",
                       "budget": 120000, "duration_m": 6}}


def _files():
    return [("files", ("proposal.txt", b"requested_budget: 120000 HKD", "text/plain")),
            ("files", ("budget.csv", b"item,amount\nMaterials,120000\n", "text/csv")),
            ("files", ("cv.txt", b"Team lead biography.", "text/plain"))]


@pytest.mark.parametrize("role,allowed", [
    ("operator", True), ("reviewer", False), ("approver", False),
    ("finance", False), ("admin", True),
])
def test_submission_role_matrix(tmp_path, role, allowed):
    client = _client(tmp_path / "cases.sqlite3", role=role)
    response = client.post("/api/cases", data={"metadata": json.dumps(_metadata(f"M-{role}"))},
                           files=_files())
    assert (response.status_code == 201) == allowed
    if not allowed:
        assert "需要角色" in response.text


def test_reviewer_can_score_but_not_sign_or_submit(tmp_path):
    operator = _client(tmp_path / "cases.sqlite3", role="operator")
    case = operator.post("/api/cases", data={"metadata": json.dumps(_metadata("R-1"))},
                         files=_files()).json()
    reviewer = _client(tmp_path / "cases.sqlite3", role="reviewer")
    programs = reviewer.get("/api/programs").json()
    criteria = {item["name"]: item["max_score"] for program in programs
                if program["program_id"] == case["program_id"]
                for item in program["criteria"]}
    scored = reviewer.put(f"/api/cases/{case['id']}/scores", json={
        "revision": case["revision"],
        "scores": [{"reviewer": f"Reviewer {letter}", "criteria": criteria, "comment": "ok."}
                   for letter in ("A", "B")]})
    assert scored.status_code == 200            # 評委可評分
    sign = reviewer.post(f"/api/cases/{case['id']}/decision", json={
        "revision": case["revision"] + 1, "outcome": "approved", "reason": "越權嘗試"})
    assert sign.status_code == 403              # 評委不可簽發


def test_approver_can_sign_but_not_score(tmp_path):
    db = tmp_path / "cases.sqlite3"
    operator = _client(db, role="operator")
    case = operator.post("/api/cases", data={"metadata": json.dumps(_metadata("A-1"))},
                         files=_files()).json()
    approver = _client(db, role="approver")
    programs = approver.get("/api/programs").json()
    criteria = {item["name"]: item["max_score"] for program in programs
                if program["program_id"] == case["program_id"]
                for item in program["criteria"]}
    denied = approver.put(f"/api/cases/{case['id']}/scores", json={
        "revision": case["revision"],
        "scores": [{"reviewer": "Reviewer A", "criteria": criteria, "comment": "ok."}]})
    assert denied.status_code == 403            # 主管不可評分
    reviewer = _client(db, role="reviewer")
    scored = reviewer.put(f"/api/cases/{case['id']}/scores", json={
        "revision": case["revision"],
        "scores": [{"reviewer": f"Reviewer {letter}", "criteria": criteria, "comment": "ok."}
                   for letter in ("A", "B")]})
    assert scored.status_code == 200            # 評委可評分
    sign = approver.post(f"/api/cases/{case['id']}/decision", json={
        "revision": _latest_revision(approver, case["id"]), "outcome": "approved",
        "actor": "主管", "reason": "主管核准"})
    assert sign.status_code == 200              # 主管可簽發


def test_finance_can_update_ledger_but_not_sign(tmp_path):
    operator = _client(tmp_path / "cases.sqlite3", role="operator")
    case = operator.post("/api/cases", data={"metadata": json.dumps(_metadata("F-1"))},
                         files=_files()).json()
    approver = _client(tmp_path / "cases.sqlite3", role="approver")
    programs = approver.get("/api/programs").json()
    criteria = {item["name"]: item["max_score"] for program in programs
                if program["program_id"] == case["program_id"]
                for item in program["criteria"]}
    reviewer = _client(tmp_path / "cases.sqlite3", role="reviewer")
    reviewer.put(f"/api/cases/{case['id']}/scores", json={
        "revision": case["revision"],
        "scores": [{"reviewer": f"Reviewer {letter}", "criteria": criteria, "comment": "ok."}
                   for letter in ("A", "B")]})
    approver.post(f"/api/cases/{case['id']}/decision", json={
        "revision": _latest_revision(approver, case["id"]), "outcome": "approved",
        "actor": "主管", "reason": "核准"})
    finance = _client(tmp_path / "cases.sqlite3", role="finance")
    denied = finance.post(f"/api/cases/{case['id']}/decision", json={
        "revision": _latest_revision(finance, case["id"]) + 1, "outcome": "approved", "reason": "財務越權簽發"})
    assert denied.status_code == 403            # 財務不可簽發
    ledger = finance.put(f"/api/cases/{case['id']}/project", json={
        "revision": _latest_revision(finance, case["id"]), "actor": "finance",
        "milestones": [{"name": "啓動", "due": "2027-01-15", "status": "done"}],
        "disbursements": [{"name": "首期", "due": "2027-01-20", "amount": 60000,
                           "paid_amount": 0, "paid_at": ""}],
        "periodic_reports": []})
    assert ledger.status_code == 200            # 財務可更新台帳


def test_admin_bypasses_role_matrix(tmp_path):
    client = _client(tmp_path / "cases.sqlite3", role="operator")
    client.app.state.auth.create_account("test-admin", "測試管理員", "test-pass", role="admin")
    admin = _client(tmp_path / "cases.sqlite3", role="admin")
    admin.headers["Authorization"] = "Basic " + base64.b64encode(b"test-admin:test-pass").decode()
    response = admin.post("/api/cases", data={"metadata": json.dumps(_metadata("ADM-1"))},
                          files=_files())
    assert response.status_code == 201          # admin 可收件
    denied = client.post("/api/auth/disable", json={"username": "admin"})
    assert denied.status_code == 403            # 非 admin 不可停用賬戶


def test_disabled_operator_cannot_submit(tmp_path):
    client = _client(tmp_path / "cases.sqlite3", role="operator")
    client.app.state.auth.set_disabled("demo", disabled=True)
    response = client.post("/api/cases", data={"metadata": json.dumps(_metadata("D-1"))},
                           files=_files())
    assert response.status_code == 403          # 停用賬號即時拒絕
    client.app.state.auth.set_disabled("demo", disabled=False)
