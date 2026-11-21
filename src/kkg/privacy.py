"""私隱與商業敏感資訊防護：在數據離開本機前偵測並去識別化。

設計原則（對應場景16「技術創新與安全」評審維度）：
- 送到第三方大模型前先脫敏，原始敏感值不離開本機
- 佔位符在同一會話內一致，LLM仍能抽取結構（如郵箱格式、金額量級）
- 原始值與佔位符的映射只存於記憶體，不落盤、不進審計日誌
- 提供 PrivacyReport 供申請人查看「發現並遮蔽了多少敏感值」

支援的敏感類型：電郵、電話、香港身份證/護照/內地身份證號、銀行卡號、
IP地址、合約編號、內地手機號、API 憑據（sk-/AIza/AWS/JWT），以及
可選的商業敏感（金額、公司名）。

安全加固：
- 預設 salt 改為每個 Redactor 實例隨機生成，避免同一敏感值在不同請求
  產生相同佔位符而被跨請求關聯；需要穩定輸出時可顯式傳入 salt。
- 新增 scan_output_leaks()：對外輸出前的最後一道閘門，確保敏感值或
  憑據沒有因模型幻覺/拼接而重新出現在待發布文字中。
"""
from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PrivacyReport:
    """去識別化報告：只記錄類型與次數，不記錄原始值。"""

    counts: dict[str, int] = field(default_factory=dict)
    mask_count: int = 0

    def summary(self) -> str:
        if not self.counts:
            return "未發現需遮蔽的敏感資訊"
        parts = [f"{k}×{v}" for k, v in sorted(self.counts.items())]
        return f"已遮蔽{self.mask_count}處敏感資訊（{'、'.join(parts)}）"


def _luhn_ok(value: str) -> bool:
    """Luhn 校驗：避免把時間戳、項目編號當成銀行卡號遮蔽。"""
    digits = re.sub(r"\D", "", value)
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        digit = int(char)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _nearby(text: str, start: int, words: tuple[str, ...], window: int = 24) -> bool:
    context = text[max(0, start - window):start].casefold()
    return any(word in context for word in words)


def _accept(kind: str, match: re.Match[str], text: str) -> bool:
    """正則命中後的語意覆核：降低誤傷普通編號的機會。"""
    if kind == "bankcard":
        digits = re.sub(r"\D", "", match.group())
        if not _luhn_ok(digits):
            return False
        # 13–14 位較可能是 Unix 時間戳或內部編號，須有卡片語境才遮蔽。
        return len(digits) >= 15 or _nearby(
            text, match.start(), ("card", "visa", "master", "unionpay", "卡", "銀聯"))
    if kind == "passport":
        return _nearby(text, match.start(), ("passport", "護照", "护照"))
    if kind == "hkid":
        # 括號校驗位是香港身份證的強特徵；沒有括號時須有身份語境，
        # 否則「項目號 A1234567」等普通編號會被誤殺。
        return "(" in match.group() or _nearby(
            text, match.start(), ("身份證", "身分證", "identity card", "hkid", "證件"))
    return True


# 各類敏感資訊的正則；順序影響遮蔽優先級（長/具體的在前）
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")),
    # 香港身份證：A1234567(3) 或 A12345673；括號校驗位一併遮蔽
    ("hkid", re.compile(r"\b[A-NP-Z]\d{6,8}(?:\([0-9A]\)|[0-9A])(?![\w])", re.I)),
    ("idcard", re.compile(r"\b\d{17}[\dXx]\b")),                      # 內地身份證
    ("passport", re.compile(r"\b[A-Z]{1,2}\d{6,9}\b")),              # 護照號
    ("bankcard", re.compile(r"\b(?:\d[ -]*?){13,19}\b")),            # 銀行卡（ lax ）
    # 香港/通用電話：前置不可為數字，後置不可為數字，兼容「電話91234567」緊貼中文
    ("phone", re.compile(r"(?<!\d)(?:\+?852[- ]?)?\d{4}[- ]?\d{4}(?!\d)")),
    ("ip", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("contract", re.compile(r"\b(?:CT|PO|INV)[-]?\d{4,}\b", re.I)),  # 合約/訂單號
    # 內地手機號：1 開頭 11 位，前置不可為數字
    ("cnphone", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    # API 憑據：OpenAI sk- / Google AIza / AWS AKIA / JWT / GitHub token
    ("apikey", re.compile(r"(?:sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,}|"
                          r"AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|"
                          r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})")),
]


class Redactor:
    """去識別化器：以HMAC式哈希生成穩定佔位符，映射只存於記憶體。"""

    def __init__(self, salt: str | None = None, mask_amounts: bool = False) -> None:
        # 預設隨機 salt：同一敏感值在不同請求不會產生相同佔位符，
        # 防止跨請求關聯；如需穩定輸出（例如測試）可顯式傳入 salt。
        self.salt = salt or secrets.token_hex(16)
        self.mask_amounts = mask_amounts
        self._map: dict[str, str] = {}     # 原始值 -> 佔位符（記憶體內，不落盤）
        self.report = PrivacyReport()

    def _token(self, value: str, kind: str) -> str:
        if value not in self._map:
            digest = hashlib.sha256(f"{self.salt}:{kind}:{value}".encode()).hexdigest()[:10]
            self._map[value] = f"[{kind.upper()}_{digest}]"
            self.report.counts[kind] = self.report.counts.get(kind, 0) + 1
            self.report.mask_count += 1
        return self._map[value]

    def redact(self, text: str) -> str:
        if not text:
            return text
        out = text
        for kind, pattern in _PATTERNS:
            def replace(match: re.Match[str], *, _kind: str = kind) -> str:
                if not _accept(_kind, match, out):
                    return match.group()
                return self._token(match.group(), _kind)
            out = pattern.sub(replace, out)
        if self.mask_amounts:
            out = re.sub(r"(?<![A-Za-z0-9])(?:\d{1,3}(?:,\d{3})+|\d{5,})(?:\.\d+)?",
                         lambda m: self._token(m.group(), "amount"), out)
        return out

    def restore(self, text: str) -> str:
        """還原佔位符（僅供本機內部顯示，例如評審包還原申請人聯絡方式）。"""
        for original, token in self._map.items():
            text = text.replace(token, original)
        return text

    def clear(self) -> None:
        """清空映射：工作階段結束時呼叫，原始值即不可還原。"""
        self._map.clear()

    def safe_view(self, app: Any) -> Any:
        """生成申請的去識別化視圖：所有欄位與附件正文以佔位符取代敏感值。

        供送LLM抽取及生成對外評審資料包使用，原始Application保持不變。
        """
        from .models import Application

        return Application(
            app_id=app.app_id, program=app.program, title=self.redact(app.title),
            applicant=self.redact(app.applicant), email=self.redact(app.email),
            team=[self.redact(t) for t in app.team],
            fields={k: self.redact(str(v)) for k, v in app.fields.items()},
            docs=[type(d)(path=d.path, name=d.name, doc_type=d.doc_type,
                          text=self.redact(d.text), meta=d.meta, error=d.error)
                  for d in app.docs],
        )


def scan_sensitive(text: str) -> dict[str, int]:
    """只掃描不遮蔽，返回各類敏感資訊命中次數（供UI提示用）。"""
    counts: dict[str, int] = {}
    for kind, pattern in _PATTERNS:
        n = sum(1 for match in pattern.finditer(text) if _accept(kind, match, text))
        if n:
            counts[kind] = n
    return counts


def redact_for_llm(app_fields: dict[str, Any], corpus: str,
                   redactor: Redactor | None = None) -> tuple[str, PrivacyReport]:
    """送LLM前的統一入口：合併表單字段與正文後整體去識別化。"""
    redactor = redactor or Redactor()
    merged = "\n".join(f"{k}: {v}" for k, v in app_fields.items()) + "\n" + corpus
    safe = redactor.redact(merged)
    return safe, redactor.report


def scan_output_leaks(text: str) -> dict[str, int]:
    """對外發布前的最後閘門：只回傳命中的類型與次數，絶不回傳原始值。

    與 scan_sensitive 共用正則；獨立命名是為了讓呼叫端語意清晰——
    在寫出評審包/Excel/通知前呼叫，若非空即應先去識別化。
    """
    return scan_sensitive(text or "")


def ensure_safe_output(text: str, redactor: Redactor | None = None) -> tuple[str, dict[str, int]]:
    """確保對外文字安全：發現敏感值即以佔位符取代，回傳 (安全文字, 命中統計)。

    這是「輸出閘門」的便捷實作：即使上游拼接到幻覺敏感值，也不會外洩。
    """
    hits = scan_output_leaks(text)
    if not hits:
        return text, {}
    redactor = redactor or Redactor()
    return redactor.redact(text), hits
