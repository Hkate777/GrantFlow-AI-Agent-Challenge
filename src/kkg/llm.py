"""可插拔大模型模組：默認MockLLM（離線可跑），支持多家主流模型供應商。

設計原則：
- 統一 complete(system, user) 接口，所有Agent邏輯與提示詞不變
- MockLLM確定性離線可復現，無API Key時整條流水線照樣可跑
- 真實模型懶加載、無密鑰時優雅降級至Mock，不中斷評審流程
- 所有HTTP調用走標準庫urllib，無額外依賴

支持的 --llm 取值見 get_llm()；any_openai兼容端點用 openai。
"""
from __future__ import annotations

import json
import os
from typing import Any, Protocol


class LLM(Protocol):
    def complete(self, system: str, user: str) -> str: ...

    name: str


class MockLLM:
    """離線確定性模型：用啓發式規則產出穩定結果，保證無API Key時整條流水線可復現。"""

    name = "mock-llm"

    def complete(self, system: str, user: str) -> str:
        if "return JSON" in system or "return json" in system.lower():
            return self._extract_json(user)
        lines = [ln.strip() for ln in user.splitlines() if ln.strip()]
        return " ".join(lines[:6])[:600]

    def _extract_json(self, text: str) -> str:
        fields: dict[str, Any] = {}
        for ln in text.splitlines():
            if ":" in ln or "：" in ln:
                sep = ":" if ":" in ln else "："
                k, _, v = ln.partition(sep)
                k = k.strip().strip("-*# ")
                v = v.strip()
                if k and v and len(k) <= 40:
                    fields[k] = v
        return json.dumps(fields, ensure_ascii=False)


class _HTTPLLM:
    """HTTP LLM基類：統一請求、超時、錯誤包裝與JSON解析。"""

    def __init__(self, name: str, model: str, key: str, timeout: int = 60) -> None:
        self.name = f"{name}:{model}"
        self.model = model
        self.key = key
        self.timeout = timeout

    def _post(self, url: str, payload: dict, headers: dict) -> dict:
        import urllib.error
        import urllib.request

        body = json.dumps(payload, ensure_ascii=False).encode()
        hdrs = {"Content-Type": "application/json", **headers}
        req = urllib.request.Request(url, data=body, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{self.name} HTTP {e.code}：{e.read().decode()[:200]}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"{self.name} 網絡錯誤：{e.reason}") from e


class OpenAILLM(_HTTPLLM):
    """OpenAI兼容端點（含Azure/OpenAI/通義/DeepSeek/Moonshot等兼容API）。"""

    def __init__(self) -> None:
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        key = os.environ.get("OPENAI_API_KEY", "")
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        if not key:
            raise RuntimeError("未設置OPENAI_API_KEY，無法使用OpenAI兼容模型。")
        super().__init__("openai", model, key)
        self.url = f"{base}/chat/completions"

    def complete(self, system: str, user: str) -> str:
        data = self._post(self.url, {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0.2,
        }, {"Authorization": f"Bearer {self.key}"})
        return data["choices"][0]["message"]["content"]


class AnthropicLLM(_HTTPLLM):
    """Anthropic Claude（Messages API）。"""

    def __init__(self) -> None:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        model = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
        if not key:
            raise RuntimeError("未設置ANTHROPIC_API_KEY，無法使用Claude。")
        super().__init__("anthropic", model, key)
        self.url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1/messages")

    def complete(self, system: str, user: str) -> str:
        data = self._post(self.url, {
            "model": self.model,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "temperature": 0.2,
            "max_tokens": 2048,
        }, {"x-api-key": self.key, "anthropic-version": "2023-06-01"})
        return "".join(b.get("text", "") for b in data.get("content", []))


class GeminiLLM(_HTTPLLM):
    """Google Gemini（generateContent API）。"""

    def __init__(self) -> None:
        key = os.environ.get("GEMINI_API_KEY", "")
        model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
        if not key:
            raise RuntimeError("未設置GEMINI_API_KEY，無法使用Gemini。")
        super().__init__("gemini", model, key)
        self.url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{model}:generateContent?key={key}")

    def complete(self, system: str, user: str) -> str:
        data = self._post(self.url, {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.2},
        }, {})
        return "".join(p.get("text", "") for c in data.get("candidates", [])
                       for p in c.get("content", {}).get("parts", []))


def get_llm(kind: str = "auto") -> LLM:
    """按kind返回模型實例；auto按環境變量自動選擇，均失敗則降級Mock。"""
    if kind == "mock":
        return MockLLM()
    factories = {
        "openai": ("OPENAI_API_KEY", OpenAILLM),
        "anthropic": ("ANTHROPIC_API_KEY", AnthropicLLM),
        "gemini": ("GEMINI_API_KEY", GeminiLLM),
    }
    order = {
        "auto": ["anthropic", "openai", "gemini"],
    }.get(kind, [kind])
    for name in order:
        env, factory = factories[name]
        if kind != name and not os.environ.get(env):  # auto模式需有密鑰才考慮
            continue
        try:
            return factory()
        except RuntimeError:
            continue
    return MockLLM()
