"""P0-11 回歸：台帳寫入的額度上限、未來日期攔截與保存往返保真。"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app


def _client(path):
    return TestClient(app.create_app(path), client=("127.0.0.1", 41000))


def _metadata(app_id="TRACK-1"):
    return {"app_id": app_id, "program_id": "HK-ACC-2027", "title": "Fixture project",
            "applicant": "Fixture team", "email": "test@example.test", "team": ["Team lead"],
            "fields": {"org_type": "university", "contact": "Team lead", "email": "test@example.test",
                       "budget": 120000, "duration_m": 6}}


def _files():
    return [("files", ("proposal.txt", b"requested_budget: 120000 HKD", "text/plain")),
            ("files", ("budget.csv", b"item,amount\nMaterials,120000\n", "text/csv")),
            ("files", ("cv.txt", b"Team lead biography.", "text/plain"))]


def _approved_case(client, tmp_path: Path, app_id="TRACK-1"):
    """提交 → 兩位評審滿分 → 人工簽發通過。回傳 case dict 與 criteria。"""
    response = client.post("/api/cases", data={"metadata": json.dumps(_metadata(app_id))},
                           files=_files())
    assert response.status_code == 201, response.text
    case = response.json()
    programs = client.get("/api/programs").json()
    criteria = {item["name"]: item["max_score"]
                for program in programs if program["program_id"] == case["program_id"]
                for item in program["criteria"]}
    client.put(f"/api/cases/{case['id']}/scores", json={
        "revision": case["revision"],
        "scores": [{"reviewer": f"Reviewer {letter}", "criteria": criteria, "comment": "ok."}
                   for letter in ("A", "B")]})
    sign = client.post(f"/api/cases/{case['id']}/decision", json={
        "revision": case["revision"] + 1, "actor": "主管", "reason": "覆核通過",
        "outcome": "approved"})
    assert sign.status_code == 200, sign.text
    return case, criteria


def _project(revision, payments=None, reports=None, milestones=None):
    return {
        "revision": revision,
        "actor": "財務經辦",
        "milestones": milestones or [
            {"name": "啓動", "due": "2027-01-15", "status": "done"},
            {"name": "結題", "due": "2027-06-30", "status": "pending"},
        ],
        "periodic_reports": reports or [
            {"name": "季度報告", "due": "2027-03-31", "submitted_at": ""},
        ],
        "disbursements": payments or [
            {"name": "首期", "due": "2027-01-20", "amount": 60000, "paid_amount": 0, "paid_at": ""},
        ],
    }


def test_future_payment_confirmation_is_rejected(tmp_path):
    """未來日期的實付確認不可保存（審評實測：讀取時被清空 → 二次保存丟值）。"""
    client = _client(tmp_path / "cases.sqlite3")
    case, _ = _approved_case(client, tmp_path)
    revision = client.get(f"/api/cases/{case['id']}").json()["revision"]
    project = _project(revision, payments=[{"name": "首期", "due": "2027-01-20", "amount": 60000,
                                            "paid_amount": 60000, "paid_at": "2099-12-31"}])
    response = client.put(f"/api/cases/{case['id']}/project", json=project)
    assert response.status_code == 422
    assert "不可晚於今天" in response.text


def test_future_report_submission_is_rejected(tmp_path):
    client = _client(tmp_path / "cases.sqlite3")
    case, _ = _approved_case(client, tmp_path)
    revision = client.get(f"/api/cases/{case['id']}").json()["revision"]
    project = _project(revision, reports=[{"name": "季度報告", "due": "2027-03-31",
                                           "submitted_at": "2099-12-31"}])
    response = client.put(f"/api/cases/{case['id']}/project", json=project)
    assert response.status_code == 422
    assert "不可晚於今天" in response.text


def test_funding_over_approved_budget_is_rejected(tmp_path):
    """撥款計劃合計 200,000 超出核准額度 120,000 → 服務端拒絕。"""
    client = _client(tmp_path / "cases.sqlite3")
    case, _ = _approved_case(client, tmp_path)
    revision = client.get(f"/api/cases/{case['id']}").json()["revision"]
    project = _project(revision, payments=[
        {"name": "首期", "due": "2027-01-20", "amount": 100000, "paid_amount": 0, "paid_at": ""},
        {"name": "尾期", "due": "2027-06-20", "amount": 100000, "paid_amount": 0, "paid_at": ""},
    ])
    response = client.put(f"/api/cases/{case['id']}/project", json=project)
    assert response.status_code == 422
    assert "超出核准額度" in response.text


def test_roundtrip_preserves_confirmed_values(tmp_path):
    """合法的昨日實付確認：保存 → 讀取 → 金額與日期原樣保留（往返保真）。"""
    from datetime import date, timedelta
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    client = _client(tmp_path / "cases.sqlite3")
    case, _ = _approved_case(client, tmp_path)
    revision = client.get(f"/api/cases/{case['id']}").json()["revision"]
    project = _project(revision, payments=[{"name": "首期", "due": "2027-01-20", "amount": 60000,
                                            "paid_amount": 45000, "paid_at": yesterday}])
    response = client.put(f"/api/cases/{case['id']}/project", json=project)
    assert response.status_code == 200, response.text

    saved = client.get(f"/api/cases/{case['id']}").json()["project"]
    row = next(item for item in saved["disbursements"] if item["name"] == "首期")
    assert row["paid_amount"] == 45000 and row["paid_at"] == yesterday
    assert row["status"] == "partial"
    assert saved["funding"]["paid"] == 45000

    # 同一畫面再次保存（表單回顯原值）→ 不丟失原輸入。
    revision = client.get(f"/api/cases/{case['id']}").json()["revision"]
    again = _project(revision, payments=[{"name": "首期", "due": "2027-01-20", "amount": 60000,
                                          "paid_amount": 45000, "paid_at": yesterday}])
    assert client.put(f"/api/cases/{case['id']}/project", json=again).status_code == 200
    row_again = client.get(f"/api/cases/{case['id']}").json()["project"]["disbursements"][0]
    assert row_again["paid_amount"] == 45000 and row_again["paid_at"] == yesterday


def test_within_budget_schedule_is_accepted(tmp_path):
    client = _client(tmp_path / "cases.sqlite3")
    case, _ = _approved_case(client, tmp_path)
    revision = client.get(f"/api/cases/{case['id']}").json()["revision"]
    project = _project(revision, payments=[
        {"name": "首期", "due": "2027-01-20", "amount": 60000, "paid_amount": 0, "paid_at": ""},
        {"name": "尾期", "due": "2027-06-20", "amount": 60000, "paid_amount": 0, "paid_at": ""},
    ])
    response = client.put(f"/api/cases/{case['id']}/project", json=project)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project"]["funding"]["scheduled"] == 120000
