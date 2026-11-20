"""字形標準化引擎回歸：地區精確轉換、誤判白名單、逐處定位、冪等性與 Web 端點。"""
import base64

import pytest
from fastapi.testclient import TestClient

import app
from kkg import script as sc


def test_supported_regions():
    assert sc.supported_regions() == ["CN", "HK", "SG", "TW"]
    assert sc.target_label("HK") == "香港繁體"
    assert sc.target_label("TW") == "台灣繁體"
    assert sc.target_label("CN") == "簡體"
    assert sc.target_label("XX") == ""


def test_hk_normalizes_simplified_to_hong_kong_traditional():
    # 簡體「网络信息」在香港標準下應為「網絡信息」
    out = sc.normalize_text("申请预算和网络信息", "HK")
    assert out == "申請預算和網絡信息"
    report = sc.audit_text("申请预算和网络信息", "HK")
    assert report.change_count >= 1
    assert any(e.original == "网络" and e.suggested == "網絡" for e in report.edits)


def test_hk_protects_official_hong_kong_spellings():
    # OpenCC s2hk 會誤轉這些香港標準字，必須被白名單保護
    text = "說明用戶，資料已脫敏，離群值已標示，資訊完整。"
    assert sc.normalize_text(text, "HK") == text
    report = sc.audit_text(text, "HK")
    assert report.change_count == 0
    assert report.summary().endswith("無需修正")


def test_tw_uses_taiwan_terms():
    out = sc.normalize_text("软件和网络，資訊質量", "TW")
    assert out == "軟體和網路，資訊質量"
    report = sc.audit_text("软件和网络", "TW")
    assert report.change_count >= 1
    # 定位需準確：所有建議都在第 1 行，且合併後原文無錯位空片段
    assert all(e.line == 1 for e in report.edits)
    assert all(e.original.strip() and e.suggested.strip() for e in report.edits)
    # 整段合併後應覆蓋「软件」與「网络」兩處變化
    joined = "".join(e.original for e in report.edits)
    assert "软" in joined and "网络" in joined


def test_cn_converts_traditional_to_simplified():
    out = sc.normalize_text("軟件網絡資訊", "CN")
    assert out == "软件网络资讯"
    report = sc.audit_text("軟件網絡", "CN")
    assert report.change_count >= 1


def test_mixed_script_is_detected_with_locations():
    report = sc.audit_text("第一行：軟件\n第二行：网络\n第三行：資訊", "HK")
    assert report.mixed_scripts is True
    assert report.line_count == 3
    net = [e for e in report.edits if e.original == "网络"]
    assert len(net) == 1
    assert net[0].line == 2  # 精確定位到第二行


def test_idempotence():
    for region in ("HK", "TW", "CN", "SG"):
        once = sc.normalize_text("軟件說明用戶，网络信息与資訊。", region)
        twice = sc.normalize_text(once, region)
        assert twice == once, f"{region} 非冪等"


def test_unsupported_region_is_noop():
    text = "軟件网络"
    assert sc.normalize_text(text, "US") == text
    report = sc.audit_text(text, "US")
    assert report.available is False
    assert report.change_count == 0


def test_report_as_dict_shape():
    report = sc.audit_text("网络信息", "HK")
    data = report.as_dict()
    assert data["region"] == "HK"
    assert data["target_label"] == "香港繁體"
    assert data["change_count"] == len(data["edits"])
    assert data["normalized_text"] == "網絡信息"
    assert data["edits"][0].keys() >= {"line", "column", "original", "suggested", "start", "end"}


def _client(path, auth=("demo", "demo")):
    client = TestClient(app.create_app(path), client=("127.0.0.1", 41000))
    if auth:
        token = base64.b64encode(":".join(auth).encode()).decode()
        client.headers["Authorization"] = f"Basic {token}"
    return client


def test_script_audit_web_endpoint(tmp_path):
    client = _client(tmp_path / "script.sqlite3")
    regions = client.get("/api/script/regions")
    assert regions.status_code == 200
    assert {r["code"] for r in regions.json()} == {"CN", "HK", "SG", "TW"}

    resp = client.post("/api/script/audit", json={"text": "軟件說明用戶，网络信息。", "region": "HK"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["change_count"] == 1
    assert data["edits"][0]["original"] == "网络"
    assert data["edits"][0]["suggested"] == "網絡"
    assert data["normalized_text"] == "軟件說明用戶，網絡信息。"


def test_script_audit_requires_auth(tmp_path):
    client = _client(tmp_path / "script.sqlite3", auth=None)
    assert client.post("/api/script/audit", json={"text": "软件", "region": "HK"}).status_code == 401


def test_script_audit_rejects_unknown_region(tmp_path):
    client = _client(tmp_path / "script.sqlite3")
    resp = client.post("/api/script/audit", json={"text": "软件", "region": "XX"})
    assert resp.status_code == 404
