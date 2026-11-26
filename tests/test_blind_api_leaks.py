"""盲評 API 端點回歸：評委在簽發前不得從列表／評審包／可追溯物料看到申請人身份。

詳情端點早有 ``blind_projection``，但列表、評審包導出與可追溯物料各有獨立的
生成路徑，曾各自繞過盲評。本模組以真實 HTTP 請求驗證三條路徑一致遮蔽。
"""
from __future__ import annotations

import base64
import io
import json
import zipfile

from fastapi.testclient import TestClient

import app as app_module
from kkg.serve import RuntimeConfig


def _hdr(user: str, password: str = "demo") -> dict:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _case_with_identity(client: TestClient) -> dict:
    """建立一個帶明顯機構名／團隊名的個案，附件名亦含機構簡稱。"""
    meta = {
        "app_id": "BLIND-LEAK-1", "program_id": "HK-ACC-2027",
        "title": "AI 文書助手（啓明科技有限公司）",
        "applicant": "啓明科技有限公司", "email": "ceo@qiming-engine.hk",
        "team": ["陳曉峯", "李嘉穎"],
        "fields": {"org_type": "company", "contact": "陳曉峯",
                   "email": "ceo@qiming-engine.hk", "budget": 120000, "duration_m": 6,
                   "project_desc": "本項目由啓明科技主導研發。"},
    }
    content = "啓明科技有限公司 遵守 CUHK 合作，預算 requested_budget: 120000 HKD".encode("utf-8")
    response = client.post(
        "/api/cases",
        data={"metadata": json.dumps(meta, ensure_ascii=False)},
        files=[("files", ("啓明科技_申請書.txt", content, "text/plain"))],
        headers=_hdr("demo"),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _client(tmp_path):
    runtime = RuntimeConfig(host="127.0.0.1", allowed_hosts=("testserver",))
    return TestClient(app_module.create_app(str(tmp_path / "db.sqlite3"), runtime=runtime),
                      client=("127.0.0.1", 41000))


def test_reviewer_case_list_hides_applicant_and_title(tmp_path):
    client = _client(tmp_path)
    case = _case_with_identity(client)
    reviewer = client.get("/api/cases", headers=_hdr("reviewer_demo")).json()
    items = reviewer["items"] if isinstance(reviewer, dict) else reviewer
    row = next(item for item in items if item["id"] == case["id"])
    assert row["applicant"].startswith("Applicant ")
    assert "啓明" not in row["title"]
    assert "weighted_total" not in row


def test_operator_case_list_keeps_identity(tmp_path):
    client = _client(tmp_path)
    case = _case_with_identity(client)
    operator = client.get("/api/cases", headers=_hdr("demo")).json()
    items = operator["items"] if isinstance(operator, dict) else operator
    row = next(item for item in items if item["id"] == case["id"])
    assert row["applicant"] == "啓明科技有限公司"


def test_reviewer_pack_export_hides_identity(tmp_path):
    client = _client(tmp_path)
    case = _case_with_identity(client)
    response = client.get(f"/api/cases/{case['id']}/pack", headers=_hdr("reviewer_demo"))
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    for name in archive.namelist():
        text = archive.read(name).decode("utf-8", "ignore")
        for term in ("啓明科技有限公司", "啓明科技", "啓明", "陳曉峯", "李嘉穎", "qiming-engine.hk"):
            assert term not in text, f"{name} 洩漏身份詞：{term}"


def test_reviewer_traceability_hides_identity_but_operator_sees_it(tmp_path):
    client = _client(tmp_path)
    case = _case_with_identity(client)
    reviewer = client.get(f"/api/cases/{case['id']}/traceability",
                          headers=_hdr("reviewer_demo")).json()
    body = json.dumps(reviewer, ensure_ascii=False)
    assert "啓明" not in body, "評委可追溯物料不得含機構簡稱"
    assert "Applicant" in body
    assert reviewer["deterministic_score"].get("withheld") is True
    operator = client.get(f"/api/cases/{case['id']}/traceability",
                          headers=_hdr("demo")).json()
    assert "啓明" in json.dumps(operator, ensure_ascii=False)
