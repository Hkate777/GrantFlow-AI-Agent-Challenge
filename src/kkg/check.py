"""完整性 & 合規性檢查Agent：按計劃規則做必填資料、附件、格式、資格、利益衝突審查。"""
from __future__ import annotations

import re
from typing import Any

from .models import Application, Finding, FindingType, Severity
from .program import Program
from .region import Region, detect_script

_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.]+$")


def check_completeness(app: Application, prog: Program) -> list[Finding]:
    findings: list[Finding] = []

    # 1. 必填字段
    for rf in prog.required_fields:
        val = app.fields.get(rf.key)
        label = rf.label or rf.key
        if val is None or (isinstance(val, str) and not val.strip()):
            findings.append(Finding(FindingType.MISSING, Severity.ERROR,
                                    f"必填字段缺失：{label}", source="required_fields", needs_human=True))
            continue
        if rf.ftype == "email" and not _EMAIL_RE.match(str(val)):
            findings.append(Finding(FindingType.FORMAT, Severity.WARNING,
                                    f"字段格式異常：{label}不是有效郵箱（{val}）",
                                    source="required_fields"))
        if rf.ftype == "number":
            try:
                num = float(str(val).replace(",", ""))
                if rf.min_value is not None and num < rf.min_value:
                    findings.append(Finding(FindingType.ANOMALY, Severity.WARNING,
                                            f"{label}={num}低於下限{rf.min_value}", source="range_check"))
                if rf.max_value is not None and num > rf.max_value:
                    findings.append(Finding(FindingType.ANOMALY, Severity.WARNING,
                                            f"{label}={num}超出上限{rf.max_value}", source="range_check"))
            except ValueError:
                findings.append(Finding(FindingType.FORMAT, Severity.WARNING,
                                        f"{label}應為數字，實際為「{val}」", source="type_check"))

    # 2. 必備附件（按文件名關鍵詞匹配）
    names = [d.name.lower() for d in app.docs]
    for rd in prog.required_docs:
        if not any(rd.name.lower() in n for n in names):
            sev = Severity.ERROR if rd.required else Severity.INFO
            findings.append(Finding(FindingType.MISSING, sev,
                                    f"{'必備' if rd.required else '建議'}附件缺失：{rd.name}",
                                    source="required_docs", needs_human=rd.required))

    # 3. 解析失敗 / 空文檔
    for d in app.docs:
        if d.error:
            findings.append(Finding(FindingType.FORMAT, Severity.WARNING,
                                    f"附件「{d.name}」無法解析（{d.error}），需人工查看",
                                    source=d.name, needs_human=True))

    # 4. 資格
    elig = prog.eligibility
    n_team = len(app.team) or 1
    if not (elig.min_team <= n_team <= elig.max_team):
        findings.append(Finding(FindingType.ELIGIBILITY, Severity.ERROR,
                                f"團隊人數{n_team}不在允許範圍{elig.min_team}–{elig.max_team}",
                                source="eligibility", needs_human=True))
    blob = (app.title + " " + " ".join(str(v) for v in app.fields.values())).lower()
    for kw in elig.excluded_keywords:
        if kw.lower() in blob:
            findings.append(Finding(FindingType.ELIGIBILITY, Severity.CRITICAL,
                                    f"觸發排除關鍵詞「{kw}」，疑似不符合申請資格",
                                    source="eligibility", needs_human=True))

    # 5. 預算上限
    if prog.budget_cap is not None:
        budget = _to_number(app.fields.get("budget"))
        if budget is not None and budget > prog.budget_cap:
            findings.append(Finding(FindingType.ANOMALY, Severity.WARNING,
                                    f"預算{budget:,.0f}超出計劃上限{prog.budget_cap:,.0f}",
                                    source="budget_cap", needs_human=True))

    # 6. 潛在利益衝突（評審人 ↔ 申請全文關鍵詞：表單字段 + 附件正文）
    full_blob = (app.title + " " + " ".join(str(v) for v in app.fields.values())
                 + " " + " ".join(d.text for d in app.docs if d.text)).lower()
    for reviewer, kws in prog.conflict_rules.items():
        for kw in kws:
            if kw.lower() in full_blob:
                findings.append(Finding(FindingType.CONFLICT, Severity.CRITICAL,
                                        f"潛在利益衝突：評審人「{reviewer}」與申請中出現的關鍵詞「{kw}」相關，"
                                        f"建議更換評審人", source="conflict_check", needs_human=True))

    return findings


def detect_duplicates(apps: list[Application]) -> list[Finding]:
    """跨申請查重：同郵箱/同標題/同申請人視為疑似重複。"""
    findings: list[Finding] = []
    seen: dict[str, list[str]] = {}
    for a in apps:
        for key in filter(None, [a.email.lower(), a.title.lower(), a.applicant.lower()]):
            seen.setdefault(key, []).append(a.app_id)
    for key, ids in seen.items():
        if len(set(ids)) > 1:
            findings.append(Finding(FindingType.DUPLICATE, Severity.WARNING,
                                    f"疑似重複申請：標識「{key[:60]}」出現在{sorted(set(ids))}",
                                    source="dedup", needs_human=True))
    return findings


def _to_number(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace("HK$", "").replace("$", "").strip())
    except ValueError:
        return None


_SCRIPT_LABEL = {"hant": "繁體", "hans": "簡體", "latin": "拉丁字母"}


def check_region(app: Application, region: Region) -> list[Finding]:
    """地區化審查：語言一致性、當地法規、用語與格式慣例。

    Agent按申請人所選地區載入對應配置（語言、法規、格式、評審慣例），
    對照申請內容給出接軌當地要求的提示。所有地區檢查均為建議性，
    不直接判定資格——最終合規與否由人工複核，符合「人機協同」原則。
    """
    findings: list[Finding] = []
    corpus = "\n".join(d.text for d in app.docs if d.text)
    title_blob = app.title + " " + " ".join(str(v) for v in app.fields.values())
    full = (title_blob + " " + corpus).lower()

    # 1. 語言一致性：只有含中日韓文字的內容才做字形比對，純拉丁字母地區不誤報。
    script = detect_script(corpus or app.title)
    if script != "latin" and script != region.script:
        findings.append(Finding(
            FindingType.FORMAT, Severity.WARNING,
            f"地區「{region.name}」期望{_SCRIPT_LABEL.get(region.script, region.script)}字形，"
            f"申請內容為{_SCRIPT_LABEL.get(script, script)}，建議統一",
            source="region.language", needs_human=False))

    # 2. 當地法規：建議性提示，列出該地區的合規框架供申請人對照，不視為硬性缺失。
    #    純拉丁字母地區（如GB/AU/US）其法規關鍵詞本就是英文，用英文全文比對；
    #    中文地區用中文全文比對。若未見關鍵詞，提示補充說明而非判定不合資格。
    if region.legal_keywords and corpus:
        if not any(kw.lower() in full for kw in region.legal_keywords):
            findings.append(Finding(
                FindingType.ELIGIBILITY, Severity.INFO,
                f"建議說明如何符合地區「{region.name}」的合規框架："
                f"{'、'.join(region.legal_keywords)}（需人工確認適用性）",
                source="region.legal", needs_human=True))

    # 3. 本地用語：將非本地詞彙標出並給出建議
    found_terms: list[str] = []
    for nonlocal_term, local in region.locale_terms.items():
        if nonlocal_term.lower() in full:
            found_terms.append(f"{nonlocal_term}至{local}")
    if found_terms:
        findings.append(Finding(
            FindingType.FORMAT, Severity.WARNING,
            f"地區「{region.name}」用語建議：{'；'.join(found_terms[:5])}",
            source="region.terms"))

    # 4. 貨幣與日期格式提示
    if region.currency and region.currency.lower() not in full and corpus:
        findings.append(Finding(
            FindingType.FORMAT, Severity.INFO,
            f"地區「{region.name}」慣用貨幣為{region.currency}，申請未見相應標示",
            source="region.format"))

    return findings
