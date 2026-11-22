import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


import base64 as _base64


def _basic_header(user: str, password: str = "demo") -> dict:
    token = _base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


# P0-07 角色矩陣的演示賬號憑據（口令均為 demo；admin 口令隨機，管理操作經 AuthStore）
ROLE_AUTH = {
    "operator": _basic_header("demo"),
    "reviewer": _basic_header("reviewer_demo"),
    "approver": _basic_header("approver_demo"),
    "finance": _basic_header("finance_demo"),
}


def make_admin(client):
    """確保 client 對應的 auth 庫存在 test-admin 賬號，返回其 Basic 頭。

    P0-07/P1-04：admin 可執行一切；批量評分更正與跨角色流程測試經此頭操作。
    """
    existing = {row["id"] for row in client.app.state.auth.list_accounts()}
    if "test-admin" not in existing:
        client.app.state.auth.create_account("test-admin", "測試管理員", "test-pass", role="admin")
    return _basic_header("test-admin", "test-pass")
