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

# 機構名後綴：涵蓋香港、內地與台灣常見的法律形式及行業標籤。
# 由詞尾逐層剝除，生成「啓明科技」「啓明」等別名；純後綴或不足兩字的
# 核心不納入，避免「科技」這類通用詞或單字造成誤傷。
_ORG_SUFFIXES = tuple(sorted((
    "股份有限公司", "有限責任公司", "有限责任公司", "有限公司",
    "公司", "集團", "集团", "控股", "實業", "实业", "企業", "企业",
    "發展", "发展", "國際", "国际", "科技", "技術", "技术",
    "網絡", "网络", "資訊", "信息",
), key=len, reverse=True))


def _alias(case_id: str) -> str:
    digest = hashlib.sha256((case_id or "").encode("utf-8")).digest()
    return f"Applicant {chr(ord('A') + digest[0] % 26)}"


def _org_variants(name: str) -> list[str]:
    """由完整機構名逐層剝除後綴生成別名。

    「啓明科技有限公司」衍生「啓明科技」與「啓明」；沒有匹配後綴的
    詞（如人名「陳曉峯」）不產生變體，避免把姓名切成無意義片段。
    """
    current = name.strip()
    variants: list[str] = []
    seen = {current}
    while True:
        nxt: str | None = None
        for suffix in _ORG_SUFFIXES:
            if current.endswith(suffix) and len(current) - len(suffix) >= 2:
                candidate = current[: -len(suffix)].strip()
                # 核心若仍屬後綴詞（「科技發展」剝成「科技」），停止剝除，
                # 避免把通用詞當成身份詞，誤遮評分所需的正文。
                if candidate in _ORG_SUFFIXES:
                    continue
                if candidate not in seen:
                    nxt = candidate
                    break
        if nxt is None:
            break
        variants.append(nxt)
        seen.add(nxt)
        current = nxt
    return variants


def _script_variants(term: str) -> list[str]:
    """生成繁簡字形變體，覆蓋自由文本與身份欄位字形不一致的情況。

    opencc 為可選依賴；缺失時只保留原詞，盲評主流程不受影響。
    """
    variants = {term}
    if not re.search(r"[\u4e00-\u9fff]", term):
        return variants
    try:
        from kkg.script import _converter
    except ImportError:
        return variants
    for config in ("t2s", "s2t", "s2twp"):
        converter = _converter(config)
        if converter is None:
            continue
        try:
            converted = converter.convert(term)
        except Exception:
            continue
        if converted:
            variants.add(converted)
    return variants


def _identity_terms(detail: dict) -> list[str]:
    """收集需要從自由文本遮蔽的身份詞（長詞優先，避免部分替換）。

    除完整身份值外，亦納入機構名剝離後綴的別名（「啓明科技有限公司」
    衍生「啓明科技」「啓明」）及其繁簡字形變體，避免評委從自由文本
    中的簡稱或異體寫法間接辨識申請人。電郵另按模式遮蔽，不限於欄位值。
    """
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

    expanded: set[str] = set()
    for term in terms:
        expanded.add(term)
        expanded.update(_org_variants(term))
    final: set[str] = set()
    for term in expanded:
        final.update(_script_variants(term))
    return sorted({term for term in final if len(term) >= 2}, key=len, reverse=True)


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
    # 移除聯繫人類別欄位值（鍵保留以維持結構，值代號化）
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
                        "detail": _scrub(finding.get("detail", ""), terms, alias),
                        # source 可能含原始檔名（如「啓明科技_申請書.txt」），
                        # 評審包導出的 findings.json 會用到，須一併遮蔽。
                        "source": _scrub(finding.get("source", ""), terms, alias)}
                       for finding in detail.get("findings", [])]
    out["scores"] = mine
    out["assessment"] = assessment
    out["blind"] = {"enabled": True, "alias": alias,
                    "note": "申請人身份已遮蔽；衝突判定由服務端按規則自動比對，不依賴申報自覺。"}
    return out


def blind_application(app: Any, detail: dict, identity: Any) -> Any:
    """回傳評審可見的 Application 視圖，供評審包導出使用。

    ``blind_projection`` 只投影個案詳情字典；評審包（summary／one_pager）另外
    從 ``Application`` 物件生成，若直接用原始物件會令機構名與團隊成員在導出時
    重新出現，繞過盲評。此函式與 ``blind_projection`` 共用同一身份詞表與代號，
    確保詳情與導出物料一致。``detail`` 須為未投影的原始細節以提取身份詞；
    非評委或已簽發則原樣回傳。
    """
    from .models import Application

    if getattr(identity, "role", None) != "reviewer" or detail.get("decision"):
        return app
    alias = _alias(str(detail.get("id") or detail.get("app_id") or ""))
    terms = _identity_terms(detail)
    fields = _scrub_value(app.fields, terms, alias)
    for key in ("contact", "org_name", "organization"):
        if key in fields:
            fields[key] = alias
    documents = []
    for doc in app.docs:
        segments = [type(seg)(locator=seg.locator,
                              text=_scrub(seg.text, terms, alias))
                    for seg in getattr(doc, "segments", [])]
        documents.append(type(doc)(path=doc.path,
                                   name=f"{doc.doc_type}（檔名已遮蔽）",
                                   doc_type=doc.doc_type,
                                   text=_scrub(doc.text, terms, alias),
                                   meta=doc.meta, error=doc.error,
                                   segments=segments))
    return Application(
        app_id=app.app_id, program=app.program,
        title=_scrub(app.title, terms, alias), applicant=alias,
        email="[電郵已遮蔽]",
        team=[f"{alias}（團隊成員已遮蔽）"] if app.team else [],
        fields=fields, docs=documents,
    )


def blind_list_item(item: dict, identity: Any) -> dict:
    """個案列表項的盲評：評委僅見代號化的申請人與已遮蔽身份詞的標題。

    列表項目只有少量欄位（無完整 detail），因此以 ``applicant`` 重建身份詞表，
    複用 ``_identity_terms`` 的簡稱／繁簡變體邏輯，確保列表與詳情代號一致。
    """
    if getattr(identity, "role", None) != "reviewer":
        return item
    out = dict(item)
    alias = _alias(str(item.get("id") or item.get("app_id") or ""))
    terms = _identity_terms({"applicant": item.get("applicant"), "fields": {}})
    out["title"] = _scrub(str(item.get("title") or ""), terms, alias)
    out["applicant"] = alias
    out.pop("weighted_total", None)  # 他人彙總分數對評委不可見
    return out


def blind_traceability(result: dict, identity: Any, detail: dict | None = None) -> dict:
    """評審在簽發前查看可追溯物料時，遮蔽確定性評分與物料中的身份詞。

    ``deterministic_score`` 源自全部暫存評分，對評委改為未提供，避免間接得知
    他人打分。此外，依據卡的 ``basis[].source/quote`` 與合規 ``excerpt`` 源自
    申請原文（含檔名），可能帶出機構簡稱——``gate_text`` 只按正則遮蔽電郵／
    電話／證件，不遮中文機構名，故須在此用身份詞表遞迴遮蔽。傳入 ``detail``
    （未投影的原始個案詳情）以提取身份詞；省略時僅保留原有分數遮蔽行為。
    非評委或已簽發個案原樣回傳。
    """
    if getattr(identity, "role", None) != "reviewer" or result.get("signed"):
        return result
    out = dict(result)
    if detail is not None:
        terms = _identity_terms(detail)
        alias = _alias(str(detail.get("id") or detail.get("app_id") or ""))
        if terms:
            out = _scrub_value(out, terms, alias)
    out["deterministic_score"] = {
        "withheld": True,
        "note": "確定性評分由全部暫存評分聚合而成，簽發前不對評委顯示。",
    }
    out["blind"] = {"enabled": True, "note": "他人評分聚合與身份詞已遮蔽。"}
    return out
