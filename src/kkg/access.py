"""個案級可見性判定（P0-02/03 的代碼側骨架）。

設計邊界
--------
角色矩陣（``auth.py``）只回答「這個人能做哪一類操作」，不回答「這個人能看
哪幾個案子」。本模組補上後者：給定一個個案與服務端身份，判定是否可見。

兩種模式：
1. **授權表模式**（``grants`` 非 ``None``）：管理員維護 ``帳號 -> 可見個案 ID
   集合`` 的白名單。表中沒有列出的帳號看不到任何個案；管理員始終可見全部。
   這是接真實資料時要填的「這個操作員只能看這幾個案子」資料級權限。
2. **指派模式**（``grants`` 為 ``None``）：沒有白名單時，評委只可見指派給自己
   的個案（``assignments`` 含其 account_id）；其餘流程角色維持全量可見。沒有
   ``assignments`` 的舊個案按舊行為處理，避免升級後既有資料忽然消失。

本層是純函數，不碰存儲、不碰 FastAPI；由 ``storage.py``／``app.py`` 呼叫。
"""
from __future__ import annotations

from typing import Any, Mapping


class CaseNotVisible(LookupError):
    """個案存在但當前身份無權查看：對外按「找不到」處理，避免枚舉。"""


ADMIN_ROLE = "admin"
REVIEWER_ROLE = "reviewer"


def _role(identity: Any) -> str:
    return str(getattr(identity, "role", "") or "").casefold()


def _account(identity: Any) -> str:
    return str(getattr(identity, "account_id", "") or "").casefold()


def can_view_case(case: dict, identity: Any,
                  grants: Mapping[str, set[str]] | None = None) -> bool:
    """判定 ``identity`` 是否可見 ``case``；不做任何修改。

    ``grants`` 為 ``None`` 表示未啟用白名單，走指派模式；給定字典（可為空）
    即啟用白名單。空字典代表尚未授權任何人（管理員除外）。
    """
    if _role(identity) == ADMIN_ROLE:
        return True
    account = _account(identity)
    if not account:
        return False
    if grants is not None:
        allowed = grants.get(account)
        return allowed is not None and str(case.get("id", "")) in allowed
    if _role(identity) != REVIEWER_ROLE:
        return True
    assignments = case.get("assignments") or []
    if not assignments:
        # 舊個案沒有指派概念，維持原有可見性。
        return True
    return any(str(row.get("reviewer_id", "")).casefold() == account
               for row in assignments if isinstance(row, Mapping))


def grant_key(account_id: str) -> str:
    """授權表鍵的正規化：與登入帳號一致，大小寫不敏感。"""
    return str(account_id or "").strip().casefold()


def normalize_grants(raw: Any) -> dict[str, set[str]]:
    """把外部傳入的授權表正規化為 ``{account_id: {case_id, ...}}``。

    接受 ``{"帳號": ["案A", "案B"]}`` 或 ``{"帳號": {"cases": [...]}}`` 兩種
    形狀；無效條目略過，不拋例外，避免管理員設定錯誤時令整個工作台不可用。
    """
    result: dict[str, set[str]] = {}
    if not isinstance(raw, Mapping):
        return result
    for account, value in raw.items():
        key = grant_key(str(account))
        if not key:
            continue
        if isinstance(value, Mapping):
            value = value.get("cases", ())
        if isinstance(value, str) or not isinstance(value, (list, tuple, set)):
            continue
        result[key] = {str(case_id) for case_id in value if str(case_id).strip()}
    return result
