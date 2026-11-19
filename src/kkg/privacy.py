"""私隱與商業敏感資訊防護：在數據離開本機前偵測並去識別化。

設計原則（對應場景16「技術創新與安全」評審維度）：
- 送到第三方大模型前先脫敏，原始敏感值不離開本機
- 佔位符在同一會話內一致，LLM仍能抽取結構（如郵箱格式、金額量級）
- 原始值與佔位符的映射只存於記憶體，不落盤、不進審計日誌
- 提供 PrivacyReport 供申請人查看「發現並遮蔽了多少敏感值」

支援的敏感類型：電郵、電話、香港身份證/護照/內地身份證號、銀行卡號、
IP地址、合約編號，以及可選的商業敏感（金額、公司名）。
"""
from __future__ import annotations

import hashlib
import re
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
]


class Redactor:
    """去識別化器：以HMAC式哈希生成穩定佔位符，映射只存於記憶體。"""

    def __init__(self, salt: str = "kkg-local-session", mask_amounts: bool = False) -> None:
        self.salt = salt
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
            out = pattern.sub(lambda m: self._token(m.group(), kind), out)
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
        n = len(pattern.findall(text))
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
