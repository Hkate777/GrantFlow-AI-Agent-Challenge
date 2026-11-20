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
