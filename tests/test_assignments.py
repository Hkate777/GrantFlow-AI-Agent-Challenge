"""P1-04 回歸：評審指派、利益申報、身份綁定的獨立評分與盲評投影。

工作流：經辦指派 reviewer 賬號 → 評審申報利益衝突（有衝突即迴避）→
各自獨立提交本人評分（服務端強制評委名=賬號顯示名）→ 攢齊 min_reviewers 後簽發。
簽發前評審只見自己的評分與狀態（盲評）；簽發後個案全量可見。
"""
import base64
import json

from fastapi.testclient import TestClient

import app
from conftest import ROLE_AUTH, make_admin


def _headers(user, password="demo"):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _client(path):
    client = TestClient(app.create_app(path), client=("127.0.0.1", 41000))
    client.headers.update(_headers("demo"))   # 經辦 demo/operator
    return client


def _metadata(app_id="ASSIGN-1"):
    return {"app_id": app_id, "program_id": "HK-ACC-2027", "title": "Fixture project",
            "applicant": "Fixture team", "email": "test@example.test", "team": ["Team lead"],
            "fields": {"org_type": "university", "contact": "Team lead", "email": "test@example.test",
                       "budget": 120000, "duration_m": 6}}


def _files():
    return [("files", ("proposal.txt", b"requested_budget: 120000 HKD", "text/plain")),
            ("files", ("budget.csv", b"item,amount\nMaterials,120000\n", "text/csv")),
            ("files", ("cv.txt", b"Team lead biography.", "text/plain"))]


def _criteria(client, case):
    programs = client.get("/api/programs").json()
    return {item["name"]: item["max_score"] for program in programs
            if program["program_id"] == case["program_id"] for item in program["criteria"]}


def _assign(client, case, reviewer_id):
    detail = client.get(f"/api/cases/{case['id']}").json()
    return client.post(f"/api/cases/{case['id']}/assignments", json={
        "revision": detail["revision"], "reviewer_id": reviewer_id})


def _declare_and_score(client, case, headers, comment="ok."):
    """評審本人：申報無衝突 → 提交本人評分（各自負責自己的 revision）。"""
    detail = client.get(f"/api/cases/{case['id']}").json()
    declared = client.post(f"/api/cases/{case['id']}/conflict", json={
        "revision": detail["revision"], "has_conflict": False}, headers=headers)
    assert declared.status_code == 200, declared.text
    return client.post(f"/api/cases/{case['id']}/scores", json={
        "revision": declared.json()["revision"],
        "criteria": _criteria(client, case), "comment": comment}, headers=headers)


def test_new_case_requires_assignment_before_scoring(tmp_path):
    client = _client(tmp_path / "cases.sqlite3")
    case = client.post("/api/cases", data={"metadata": json.dumps(_metadata())},
                       files=_files()).json()
    # 新工作流個案：未指派 → 首要狀態待指派評審。
    assert case["assessment"]["status"] == "pending_assignment"
    assert case["assignments"] == []
    unassigned = client.post(f"/api/cases/{case['id']}/conflict", json={
        "revision": case["revision"], "has_conflict": False}, headers=ROLE_AUTH["reviewer"])
    assert unassigned.status_code == 422          # 未被指派的評審不可申報/評分
    unassigned_score = client.post(f"/api/cases/{case['id']}/scores", json={
        "revision": case["revision"], "criteria": {"創新性": 20}}, headers=ROLE_AUTH["reviewer"])
    assert unassigned_score.status_code == 422


def test_assignment_lifecycle_reaches_signoff(tmp_path):
    client = _client(tmp_path / "cases.sqlite3")
    case = client.post("/api/cases", data={"metadata": json.dumps(_metadata())},
                       files=_files()).json()
    assert _assign(client, case, "reviewer_demo").status_code == 200
    assert _assign(client, case, "reviewer2_demo").status_code == 200
    detail = client.get(f"/api/cases/{case['id']}").json()
    assert detail["assessment"]["status"] == "pending_scores"   # 已指派足額 → 待評分
    assert [row["status"] for row in detail["assignments"]] == ["assigned", "assigned"]
    assert detail["assignments"][0]["display_name"] == "評審員（演示）"

    first = _declare_and_score(client, case, ROLE_AUTH["reviewer"])
    assert first.status_code == 200, first.text
    mid = client.get(f"/api/cases/{case['id']}").json()
    assert mid["assessment"]["status"] == "insufficient_reviews"   # 一人未交
    second = _declare_and_score(client, case, _headers("reviewer2_demo"))
    assert second.status_code == 200, second.text
    done = client.get(f"/api/cases/{case['id']}").json()
    assert done["assessment"]["status"] == "awaiting_decision"
    assert [row["status"] for row in done["assignments"]] == ["submitted", "submitted"]
    assert len(done["scores"]) == 2

    sign = client.post(f"/api/cases/{case['id']}/decision", json={
        "revision": done["revision"], "outcome": "approved",
        "actor": "主管", "reason": "兩位評審一致"}, headers=ROLE_AUTH["approver"])
    assert sign.status_code == 200, sign.text


def test_duplicate_assignment_and_bad_accounts_are_rejected(tmp_path):
    client = _client(tmp_path / "cases.sqlite3")
    case = client.post("/api/cases", data={"metadata": json.dumps(_metadata())},
                       files=_files()).json()
    assert _assign(client, case, "reviewer_demo").status_code == 200
    duplicate = _assign(client, case, "reviewer_demo")
    assert duplicate.status_code == 422           # 重複指派
    wrong_role = _assign(client, case, "demo")
    assert wrong_role.status_code == 422          # 非 reviewer 賬號不可指派
    missing = _assign(client, case, "ghost-reviewer")
    assert missing.status_code == 422             # 賬號不存在
    reviewers = client.get("/api/reviewers").json()
    assert {row["reviewer_id"] for row in reviewers} >= {"reviewer_demo", "reviewer2_demo"}
    forbidden = client.get("/api/reviewers", headers=ROLE_AUTH["reviewer"])
    assert forbidden.status_code == 403           # 評審不可查看可指派名單


def test_blind_review_projection(tmp_path):
    client = _client(tmp_path / "cases.sqlite3")
    case = client.post("/api/cases", data={"metadata": json.dumps(_metadata())},
                       files=_files()).json()
    assert _assign(client, case, "reviewer_demo").status_code == 200
    assert _assign(client, case, "reviewer2_demo").status_code == 200
    first = _declare_and_score(client, case, ROLE_AUTH["reviewer"], comment="評審甲意見")
    assert first.status_code == 200
    second = _declare_and_score(client, case, _headers("reviewer2_demo"), comment="評審乙意見")
    assert second.status_code == 200

    # 盲評：評審只見自己的評分與狀態，不見他人評分/加權總分/分歧。
    mine = client.get(f"/api/cases/{case['id']}", headers=ROLE_AUTH["reviewer"]).json()
    assert len(mine["scores"]) == 1
    assert mine["scores"][0]["reviewer_id"] == "reviewer_demo"
    assert mine["scores"][0]["comment"] == "評審甲意見"
    assert "weighted_total" not in mine["assessment"]
    assert "flags" not in mine["assessment"]
    assert "status" in mine["assessment"]
    listed = client.get("/api/cases", headers=ROLE_AUTH["reviewer"]).json()
    assert all("weighted_total" not in item for item in listed)
    # 經辦/主管視圖不變：全量評分與彙總可見。
    full = client.get(f"/api/cases/{case['id']}").json()
    assert len(full["scores"]) == 2
    assert full["assessment"]["weighted_total"] is not None
    # 評審導出的評審包同樣盲評：review.json 不含彙總。
    import io
    import zipfile
    pack = client.get(f"/api/cases/{case['id']}/pack", headers=ROLE_AUTH["reviewer"])
    assert pack.status_code == 200
    with zipfile.ZipFile(io.BytesIO(pack.content)) as archive:
        review = json.loads(archive.read("review.json").decode("utf-8"))
    assert "weighted_total" not in review

    # 簽發後盲評解除：評審可見全量記錄（決定後透明）。
    sign = client.post(f"/api/cases/{case['id']}/decision", json={
        "revision": full["revision"], "outcome": "approved",
        "actor": "主管", "reason": "覆核通過"}, headers=ROLE_AUTH["approver"])
    assert sign.status_code == 200, sign.text
    released = client.get(f"/api/cases/{case['id']}", headers=ROLE_AUTH["reviewer"]).json()
    assert len(released["scores"]) == 2
    assert "weighted_total" in released["assessment"]


def test_upsert_replaces_own_row_and_admin_corrects(tmp_path):
    client = _client(tmp_path / "cases.sqlite3")
    case = client.post("/api/cases", data={"metadata": json.dumps(_metadata())},
                       files=_files()).json()
    assert _assign(client, case, "reviewer_demo").status_code == 200
    first = _declare_and_score(client, case, ROLE_AUTH["reviewer"])
    assert first.status_code == 200
    revisions_before = first.json()["revision"]
    resent = _declare_and_score(client, case, ROLE_AUTH["reviewer"], comment="更正意見")
    assert resent.status_code == 200, resent.text
    detail = resent.json()
    assert len(detail["scores"]) == 1             # upsert：仍是自己的那一行
    assert detail["scores"][0]["comment"] == "更正意見"
    assert detail["revision"] == revisions_before + 2   # 重新申報+重新評分各計一次版本

    # 管理員批量更正通道仍可用，可整組重寫。
    corrected = client.put(f"/api/cases/{case['id']}/scores", json={
        "revision": detail["revision"],
        "scores": [{"reviewer": "Reviewer A", "criteria": _criteria(client, case), "comment": "c"},
                   {"reviewer": "Reviewer B", "criteria": _criteria(client, case), "comment": "c"}]},
        headers=make_admin(client))
    assert corrected.status_code == 200, corrected.text
    assert len(corrected.json()["scores"]) == 2


def test_conflict_declaration_recuses_and_removes_scores(tmp_path):
    client = _client(tmp_path / "cases.sqlite3")
    case = client.post("/api/cases", data={"metadata": json.dumps(_metadata())},
                       files=_files()).json()
    assert _assign(client, case, "reviewer_demo").status_code == 200
    scored = _declare_and_score(client, case, ROLE_AUTH["reviewer"])
    assert scored.status_code == 200
    recused = client.post(f"/api/cases/{case['id']}/conflict", json={
        "revision": scored.json()["revision"], "has_conflict": True,
        "note": "與申請團隊同一機構"}, headers=ROLE_AUTH["reviewer"])
    assert recused.status_code == 200, recused.text
    detail = recused.json()
    assert detail["scores"] == []                 # 已交評分隨迴避移除
    assert detail["assignments"][0]["status"] == "recused"
    assert detail["assignments"][0]["note"] == "與申請團隊同一機構"
    assert detail["assessment"]["status"] == "pending_assignment"  # 活躍指派不足，須替補
    blocked = client.post(f"/api/cases/{case['id']}/scores", json={
        "revision": detail["revision"], "criteria": _criteria(client, case)},
        headers=ROLE_AUTH["reviewer"])
    assert blocked.status_code == 422             # 迴避後不可再評分
    # 替補另一位評審後流程可續行。
    assert _assign(client, case, "reviewer2_demo").status_code == 200
    replacement = _declare_and_score(client, case, _headers("reviewer2_demo"))
    assert replacement.status_code == 200
    # 替補者已交一分，但活躍指派（1）仍低於 min_reviewers（2）→ 維持待指派。
    assert replacement.json()["assessment"]["status"] == "pending_assignment"
    # 迴避者不可被重複指派回同一個案。
    reassigned = _assign(client, case, "reviewer_demo")
    assert reassigned.status_code == 422
