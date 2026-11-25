"""真盲評投影：評審在簽發前看不到申請人身份，只見匿名代號與評分所需內容。

設計取捨：
- 衝突判定不靠評委自報——``check._conflict_findings`` 已按 ``conflict_rules``
  關鍵詞在服務端自動比對，並以結構化 ``subject_id`` 標識評審；申報制保留為
  額外一層，但盲評不再依賴評委「看得見申請人是誰」才能自查。
- 身份資訊（機構、電郵、團隊、聯繫人）在投影時以穩定代號取代；評分所需的
  項目內容、預算、週期與抽取結果保留，否則評委無從評分。
- 他人評分、加權總分、分歧標記在簽發前一併遮蔽；簽發後回傳全量（決定透明）。

代號由個案 ID 決定，同一評委多次查看同一案得到一致的 ``Applicant X``，
避免同一申請在不同端點出現不同代號而造成混淆。此代號只用於展示，不參與
任何規則判斷或審計。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

_ASSESSMENT_PUBLIC = ("status", "holds", "todos")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _alias(case_id: str) -> str:
    digest = hashlib.sha256((case_id or "").encode("utf-8")).digest()
    return f"Applicant {chr(ord('A') + digest[0] % 26)}"


def _identity_terms(detail: dict) -> list[str]:
    """收集需要從自由文本遮蔽的身份詞（長詞優先，避免部分替換）。"""
    app = detail.get("application") or {}
    terms: list[str] = []
    applicant = detail.get("applicant") or app.get("applicant")
    if applicant:
        terms.append(str(applicant))
    for member in detail.get("team") or app.get("team") or []:
        if member:
            terms.append(str(member))
    fields = detail.get("fields") or app.get("fields") or {}
    for key in ("contact", "applicant", "org_name", "organization"):
        if fields.get(key):
            terms.append(str(fields[key]))
    # 電郵按模式遮蔽（不限於欄位值，覆蓋自由文本中的地址）
    return sorted({term for term in terms if len(term) >= 2}, key=len, reverse=True)


def _scrub(text: str, terms: list[str], alias: str) -> str:
    if not isinstance(text, str):
        return text
    result = text
    for term in terms:
        if term:
            result = re.sub(re.escape(term), alias, result, flags=re.IGNORECASE)
    result = _EMAIL_RE.sub("[電郵已遮蔽]", result)
    return result


def _scrub_value(value: Any, terms: list[str], alias: str) -> Any:
    """遞迴遮蔽巢狀結構中的字串（含 ``_evidence`` 的引文），保留數值型評分依據。"""
    if isinstance(value, str):
        return _scrub(value, terms, alias)
    if isinstance(value, list):
        return [_scrub_value(item, terms, alias) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_value(item, terms, alias) for key, item in value.items()}
    return value


def blind_projection(detail: dict, identity: Any) -> dict:
    """回傳評審可見的盲評視圖；非評委或已簽發則原樣回傳。

    ``identity`` 需有 ``role`` 與 ``account_id``（與 ``kkg.auth.Identity`` 相容）。
    """
    if getattr(identity, "role", None) != "reviewer" or detail.get("decision"):
        return detail

    alias = _alias(str(detail.get("id") or detail.get("app_id") or ""))
    terms = _identity_terms(detail)

    # 只保留本人的評分；他人評分與彙總一律移除。
    mine = [row for row in detail.get("scores", [])
            if row.get("reviewer_id") == getattr(identity, "account_id", None)]
    assessment = {key: detail["assessment"][key] for key in _ASSESSMENT_PUBLIC
                  if key in detail.get("assessment", {})}

    # 身份欄位直接代號化；其餘結構保留但遮掉自由文本中的身份詞。
    out = dict(detail)
    out["applicant"] = alias
    out["email"] = "[電郵已遮蔽]"
    out["team"] = ["[團隊成員已遮蔽]"] if detail.get("team") else []
    out["fields"] = _scrub_value(detail.get("fields", {}), terms, alias)
    # 移除联系人类字段值（键保留以维持结构，值代号化）
    for key in ("contact", "org_name", "organization"):
        if key in out["fields"]:
            out["fields"][key] = alias
    out["title"] = _scrub(detail.get("title", ""), terms, alias)
    out["extracted"] = _scrub_value(detail.get("extracted", {}), terms, alias)
    out["documents"] = [{**doc, "name": f"{doc.get('doc_type', 'file')}（檔名已遮蔽）"}
                        for doc in detail.get("documents", [])]
    # findings 的訊息可能帶出機構/Email（如利益衝突命中關鍵詞），一併遮蔽；
    # 但保留 finding_type/severity/subject_id 供前端顯示狀態。
    out["findings"] = [{**finding,
                        "message": _scrub(finding.get("message", ""), terms, alias),
                        "detail": _scrub(finding.get("detail", ""), terms, alias)}
                       for finding in detail.get("findings", [])]
    out["scores"] = mine
    out["assessment"] = assessment
    out["blind"] = {"enabled": True, "alias": alias,
                    "note": "申請人身份已遮蔽；衝突判定由服務端按規則自動比對，不依賴申報自覺。"}
    return out


def blind_traceability(result: dict, identity: Any) -> dict:
    """評審在簽發前查看可追溯物料時，遮蔽由他人評分聚合而成的確定性評分。

    依據卡、合規分級與 Trace 規則結論不涉及申請人身份或他人分數，保留可見；
    ``deterministic_score`` 源自全部暫存評分，對評委改為未提供，避免間接
    得知他人打分。非評委或已簽發個案原樣回傳。
    """
    if getattr(identity, "role", None) != "reviewer" or result.get("signed"):
        return result
    out = dict(result)
    out["deterministic_score"] = {
        "withheld": True,
        "note": "確定性評分由全部暫存評分聚合而成，簽發前不對評委顯示。",
    }
    out["blind"] = {"enabled": True, "note": "他人評分聚合已遮蔽。"}
    return out
