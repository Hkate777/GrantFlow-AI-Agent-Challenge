"""回歸測試：審評清單的四個實測缺陷修復。

1. 截止時間以服務端收件時間強制（不再依賴申請人自報 submitted_at）。
2. 計劃級 min_reviewers：低於下限的個案不可簽發。
3. 計劃配置移除後，歷史個案仍可用隨案快照匯出評審包。
4. 個案列表支援 SQL 分頁（limit/offset + total），不帶參數保持舊形狀。
"""
import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app
from kkg.models import ReviewScore
from kkg.program import Program
from kkg.workspace import Workspace
from kkg.storage import CaseStore


def _client(path, configs=None):
    # P0-06：所有 /api 需認證——測試默認以演示賬號 demo/demo 走 HTTP Basic。
    client = TestClient(app.create_app(path, configs=configs), client=("127.0.0.1", 41000))
    client.headers["Authorization"] = "Basic " + base64.b64encode(b"demo:demo").decode()
    return client


def _metadata(app_id="OVERDUE-1", program="HK-ACC-2027"):
    return {"app_id": app_id, "program_id": program, "title": "Fixture project", "applicant": "Fixture team",
            "email": "test@example.test", "team": ["Team lead"],
            "fields": {"org_type": "university", "contact": "Team lead", "email": "test@example.test",
                       "budget": 120000, "duration_m": 6}}


def _files():
    return [("files", ("proposal.txt", b"requested_budget: 120000 HKD", "text/plain")),
            ("files", ("budget.csv", b"item,amount\nMaterials,120000\n", "text/csv")),
            ("files", ("cv.txt", b"Team lead biography.", "text/plain"))]


def _criteria(program: Program) -> dict[str, float]:
    return {item.name: item.max_score for item in program.criteria}


def _scores(program: Program, count: int = 1) -> list[dict]:
    return [{"reviewer": f"Reviewer {index}", "criteria": _criteria(program), "comment": "ok."}
            for index in range(1, count + 1)]


# ---------- 缺陷 1：截止時間服務端強制 ----------


def test_overdue_receipt_time_blocks_signoff(tmp_path):
    """計劃截止已過：即使申請人未填 submitted_at，服務端收件時間也會產生逾期發現。"""
    database = tmp_path / "cases.sqlite3"
    client = _client(database)
    response = _client(database).post("/api/cases", data={"metadata": json.dumps(_metadata())},
                                      files=_files())
    assert response.status_code == 201
    case = response.json()

    # 把收件時間改到截止之前/之後，直接驗證評估邏輯。
    from kkg.storage import CaseStore as Store
    with Store(str(database)).transaction(write=True) as connection:
        record = Store.get(connection, case["id"])
        record["received_at"] = "2020-01-01T00:00:00+00:00"   # 截止（2027-01-03）之前
        Store.update(connection, record)
    detail = client.get(f"/api/cases/{case['id']}").json()
    assert not [f for f in detail["findings"] if f["source"] == "deadline"]

    with Store(str(database)).transaction(write=True) as connection:
        record = Store.get(connection, case["id"])
        record["received_at"] = "2027-02-01T00:00:00+00:00"   # 截止之後
        Store.update(connection, record)
    detail = client.get(f"/api/cases/{case['id']}").json()
    overdue = [f for f in detail["findings"] if f["source"] == "deadline"]
    assert overdue and overdue[0]["severity"] == "error"
    # 逾期屬資格問題：簽發須被資格待核阻擋
    assert "needs_compliance" in detail["assessment"]["holds"]


# ---------- 缺陷 2：計劃級 min_reviewers ----------


def _write_program(directory: Path, min_reviewers: int) -> Path:
    source = Path(__file__).resolve().parents[1] / "configs" / "program_demo.yaml"
    text = source.read_text(encoding="utf-8-sig")
    text += f"\nmin_reviewers: {min_reviewers}\n"
    target = directory / "program_pilot.yaml"
    target.write_text(text, encoding="utf-8")
    return target


def test_min_reviewers_blocks_signoff(tmp_path):
    """min_reviewers=2 時，單一評審滿分個案不得簽發。"""
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_program(config_dir, min_reviewers=2)
    client = _client(tmp_path / "cases.sqlite3", configs=config_dir)
    response = client.post("/api/cases", data={"metadata": json.dumps(
        _metadata("PILOT-1", "HK-ACC-2027"))}, files=_files())
    assert response.status_code == 201, response.text
    case = response.json()
    criteria = _criteria(next(iter(app._programs(config_dir).values())))

    client.put(f"/api/cases/{case['id']}/scores", json={
        "revision": case["revision"], "scores": _scores_from(criteria, count=1)})
    holds = client.get(f"/api/cases/{case['id']}").json()["assessment"]["holds"]
    assert "insufficient_reviews" in holds, holds
    sign = client.post(f"/api/cases/{case['id']}/decision", json={
        "revision": case["revision"] + 1, "actor": "主管", "reason": "嘗試簽發", "outcome": "approved"})
    assert sign.status_code == 409

    # 補足第二位評審後可簽發（乾淨個案毋須複議記錄）。
    client.put(f"/api/cases/{case['id']}/scores", json={
        "revision": case["revision"] + 1, "scores": _scores_from(criteria, count=2)})
    sign = client.post(f"/api/cases/{case['id']}/decision", json={
        "revision": case["revision"] + 2, "actor": "主管", "reason": "兩位評審一致通過", "outcome": "approved"})
    assert sign.status_code == 200


def _scores_from(criteria: dict[str, float], count: int) -> list[dict]:
    return [{"reviewer": f"Reviewer {index}", "criteria": criteria, "comment": "ok."}
            for index in range(1, count + 1)]


# ---------- 缺陷 3：計劃快照 → 歷史評審包導出 ----------


def test_pack_export_survives_plan_removal(tmp_path):
    """簽發後移除計劃配置並重啟，評審包導出仍可用（快照重建），不再 422。"""
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_program(config_dir, min_reviewers=1)
    database = tmp_path / "cases.sqlite3"
    client = _client(database, configs=config_dir)
    response = client.post("/api/cases", data={"metadata": json.dumps(
        _metadata("SNAPSHOT-1", "HK-ACC-2027"))}, files=_files())
    case = response.json()
    criteria = _criteria(next(iter(app._programs(config_dir).values())))
    client.put(f"/api/cases/{case['id']}/scores", json={
        "revision": case["revision"], "scores": _scores_from(criteria, count=1)})
    sign = client.post(f"/api/cases/{case['id']}/decision", json={
        "revision": case["revision"] + 1, "actor": "主管", "reason": "通過", "outcome": "approved"})
    assert sign.status_code == 200

    # 移除計劃配置（快照仍在個案內），用僅含其他計劃的設定重啟。
    (config_dir / "program_pilot.yaml").unlink()
    (config_dir / "program_demo.yaml").write_text(
        (Path(__file__).resolve().parents[1] / "configs" / "program_demo.yaml").read_text(encoding="utf-8-sig"),
        encoding="utf-8")
    fresh = TestClient(app.create_app(tmp_path / "cases.sqlite3", configs=config_dir),
                       client=("127.0.0.1", 41000))
    fresh.headers["Authorization"] = "Basic " + base64.b64encode(b"demo:demo").decode()
    assert fresh.get(f"/api/cases/{case['id']}").status_code == 200
    export = fresh.get(f"/api/cases/{case['id']}/pack")
    assert export.status_code == 200
    assert export.headers["content-type"] == "application/zip"


# ---------- 缺陷 4：個案列表分頁 ----------


def test_case_list_pagination(tmp_path):
    client = _client(tmp_path / "cases.sqlite3")
    for index in range(5):
        response = client.post("/api/cases", data={"metadata": json.dumps(
            _metadata(f"PAGE-{index}"))}, files=_files())
        assert response.status_code == 201

    full = client.get("/api/cases")
    assert full.status_code == 200
    assert isinstance(full.json(), list) and len(full.json()) == 5     # 舊形狀：純陣列

    page = client.get("/api/cases", params={"limit": 2, "offset": 2})
    body = page.json()
    assert set(body) == {"items", "total", "limit", "offset"}
    assert body["total"] == 5 and body["limit"] == 2 and body["offset"] == 2
    assert len(body["items"]) == 2
    # offset=0 與 offset=2 的窗口不重疊
    ids_first = {item["id"] for item in
                 client.get("/api/cases", params={"limit": 2, "offset": 0}).json()["items"]}
    ids_second = {item["id"] for item in body["items"]}
    assert not ids_first & ids_second


# ---------- 輔助：Program 快照往返 ----------


def test_program_snapshot_roundtrip():
    program = Program.load(Path(__file__).resolve().parents[1] / "configs" / "program_demo.yaml")
    from dataclasses import asdict
    rebuilt = Program.from_snapshot(asdict(program))
    assert rebuilt.rule_version == program.rule_version
    assert rebuilt.min_reviewers == program.min_reviewers
