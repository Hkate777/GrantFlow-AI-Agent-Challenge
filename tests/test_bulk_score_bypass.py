"""P1-04 回歸：批量 PUT /scores 不得繞過評審工作流。

遠端部署時評委只能走 POST /scores（指派→申報→身份綁定）；PUT 批量通道
收窄為經辦/管理員更正用。本機演示保留便捷通道，但這不是安全的評審路徑。
"""
import base64
import json

from fastapi.testclient import TestClient

import app
from conftest import make_admin
from kkg.serve import RuntimeConfig
from test_assignments import _metadata, _files, _criteria


def _headers(user, password):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _rt(remote):
    return RuntimeConfig(host="0.0.0.0" if remote else "127.0.0.1",
                         allowed_hosts=("testserver",))


def _new_case(client, headers):
    client.post("/api/demo", headers=headers)
    return client.post("/api/cases", data={"metadata": json.dumps(_metadata())},
                       files=_files(), headers=headers).json()


def _criteria_for_case(client, case):
    return {item["name"]: item["max_score"]
            for p in client.get("/api/programs").json()
            if p["program_id"] == case["program_id"]
            for item in p["criteria"]}


def _remote_client(tmp_path, *, with_case=True):
    """本機建庫+種子，再以遠端模式打開；建立真實（非演示）角色賬號供測試。"""
    db = str(tmp_path / "r.sqlite3")
    local = TestClient(app.create_app(db, runtime=_rt(False)), client=("127.0.0.1", 41000))
    demo_h = _headers("demo", "demo")
    case = _new_case(local, demo_h) if with_case else None

    remote = TestClient(app.create_app(db, runtime=_rt(True)), client=("127.0.0.1", 41000))
    auth = remote.app.state.auth
    # 真實账号（非弱口令演示），不被遠端停用邏輯影響
    auth.create_account("rev_live", "評委甲", "strong-pass-1", role="reviewer")
    auth.create_account("op_live", "經辦甲", "strong-pass-2", role="operator")
    remote.headers.update(_headers("op_live", "strong-pass-2"))  # 預設可讀 /api/programs
    return remote, case, {
        "reviewer": _headers("rev_live", "strong-pass-1"),
        "operator": _headers("op_live", "strong-pass-2"),
        "admin": make_admin(remote),
    }


def test_remote_reviewer_cannot_use_bulk_put_to_bypass_workflow(tmp_path):
    """核心漏洞：遠端模式下評委用 PUT 自填姓名寫入評分，必須被擋。"""
    remote, case, H = _remote_client(tmp_path)
    crit = _criteria_for_case(remote, case)
    resp = remote.put(f"/api/cases/{case['id']}/scores",
                      json={"revision": case["revision"],
                            "scores": [{"reviewer": "FakeName", "criteria": crit, "comment": "繞過"}]},
                      headers=H["reviewer"])
    assert resp.status_code == 403, resp.text
    after = remote.get(f"/api/cases/{case['id']}", headers=H["operator"]).json()
    assert after["scores"] == [], "評委不得繞過工作流寫入任何評分"
    assert after["assignments"] == []


def test_remote_operator_bulk_put_still_works_for_correction(tmp_path):
    remote, case, H = _remote_client(tmp_path)
    crit = _criteria_for_case(remote, case)
    resp = remote.put(f"/api/cases/{case['id']}/scores",
                      json={"revision": case["revision"],
                            "scores": [{"reviewer": "A", "criteria": crit, "comment": "更正"}]},
                      headers=H["operator"])
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["scores"]) == 1


def test_remote_admin_bulk_put_still_works(tmp_path):
    """遠端模式下 admin 被允許用 PUT（不會因角色被擋）；身份綁定仍強制單一行。"""
    remote, case, H = _remote_client(tmp_path)
    crit = _criteria_for_case(remote, case)
    resp = remote.put(f"/api/cases/{case['id']}/scores",
                      json={"revision": case["revision"],
                            "scores": [{"reviewer": "A", "criteria": crit}]},
                      headers=H["admin"])
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["scores"]) == 1
    # 多行時遠端強制綁定同一登入賬號而被拒（既有身份綁定設計，非本次授權改動）
    detail = remote.get(f"/api/cases/{case['id']}").json()
    dup = remote.put(f"/api/cases/{case['id']}/scores",
                     json={"revision": detail["revision"],
                           "scores": [{"reviewer": "A", "criteria": crit},
                                      {"reviewer": "B", "criteria": crit}]},
                     headers=H["admin"])
    assert dup.status_code == 422


def test_remote_approver_cannot_bulk_put(tmp_path):
    remote, case, H = _remote_client(tmp_path)
    auth = remote.app.state.auth
    auth.create_account("appr_live", "主管甲", "strong-pass-3", role="approver")
    crit = _criteria_for_case(remote, case)
    resp = remote.put(f"/api/cases/{case['id']}/scores",
                      json={"revision": case["revision"],
                            "scores": [{"reviewer": "A", "criteria": crit}]},
                      headers=_headers("appr_live", "strong-pass-3"))
    assert resp.status_code == 403


def test_local_demo_preserves_reviewer_bulk_put(tmp_path):
    """本機演示保留評委便捷通道（並非安全評審路徑）。"""
    db = str(tmp_path / "l.sqlite3")
    local = TestClient(app.create_app(db, runtime=_rt(False)), client=("127.0.0.1", 41000))
    local.headers.update(_headers("demo", "demo"))
    case = _new_case(local, _headers("demo", "demo"))
    crit = _criteria_for_case(local, case)
    resp = local.put(f"/api/cases/{case['id']}/scores",
                     json={"revision": case["revision"],
                           "scores": [{"reviewer": "Reviewer A", "criteria": crit}]},
                     headers=_headers("reviewer_demo", "demo"))
    assert resp.status_code == 200, resp.text


def test_remote_migrated_local_db_disables_all_demo_accounts(tmp_path):
    """本機庫遷移到遠端後，所有弱口令演示賬號（含 reviewer2_demo）都須停用。"""
    db = str(tmp_path / "m.sqlite3")
    local = TestClient(app.create_app(db, runtime=_rt(False)), client=("127.0.0.1", 41000))
    local.post("/api/demo", headers=_headers("demo", "demo"))
    remote = TestClient(app.create_app(db, runtime=_rt(True)), client=("127.0.0.1", 41000))
    rows = {row["id"]: bool(row["disabled"]) for row in remote.app.state.auth.list_accounts()}
    for account in ("demo", "reviewer_demo", "reviewer2_demo", "approver_demo", "finance_demo"):
        assert rows.get(account) is True, f"遠端模式下 {account} 應被停用"


def test_local_enforce_identity_switch_makes_single_machine_strict(tmp_path):
    """單機也能用 GRANTFLOW_ENFORCE_IDENTITY 一鍵啟用身份綁定，不依賴遠端監聽。"""
    db = str(tmp_path / "e.sqlite3")
    strict = RuntimeConfig(host="127.0.0.1", enforce_identity=True)
    assert strict.strict_identity is True
    assert strict.remote_access is False  # 網路仍視為本機
    client = TestClient(app.create_app(db, runtime=strict), client=("127.0.0.1", 41000))
    rows = {row["id"]: bool(row["disabled"]) for row in client.app.state.auth.list_accounts()}
    # 嚴格模式下不建立弱口令演示賬號
    assert "demo" not in rows or rows["demo"] is True
    assert "reviewer2_demo" not in rows or rows["reviewer2_demo"] is True


def test_host_override_to_non_loopback_recomputes_strict_identity(tmp_path):
    """--host 0.0.0.0 覆蓋監聽地址時，須按新 host 重建 runtime 並停用演示賬號。

    模組導入時按 loopback 構造的 app 若直接拿去對外監聽，strict_identity 仍為
    False，演示弱口令賬號未停用、批量評分走本機寬鬆分支，形成安全漏洞。
    """
    from dataclasses import replace

    db = str(tmp_path / "host.sqlite3")
    local_rt = RuntimeConfig(host="127.0.0.1", allowed_hosts=("testserver",))
    app.create_app(db, runtime=local_rt)  # 建立本機演示賬號

    remote_rt = replace(local_rt, host="0.0.0.0")
    assert remote_rt.strict_identity is True
    remote = TestClient(app.create_app(db, runtime=remote_rt), client=("127.0.0.1", 41000))
    rows = {row["id"]: bool(row["disabled"]) for row in remote.app.state.auth.list_accounts()}
    assert rows.get("reviewer_demo") is True, "對外監聽時演示評委賬號須停用"
    demo_login = remote.get("/api/auth/me", headers=_headers("reviewer_demo", "demo"))
    assert demo_login.status_code == 403
