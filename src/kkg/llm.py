"""可插拔 LLM 客户端：默认 MockLLM（离线可跑），可切换任意 OpenAI 兼容端点。"""
from __future__ import annotations

import json
import os
from typing import Any, Protocol


class LLM(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class MockLLM:
    """离线确定性模型：用启发式规则产出稳定结果，保证无 API Key 时整条流水线可复现。

    真实运行时替换为 OpenAILLM 即可，所有 Agent 逻辑（提示词、输出解析、校验）不变。
    """

    name = "mock-llm"

    def complete(self, system: str, user: str) -> str:
        # 提取类调用：要求返回 JSON 时，从文本里做基于关键词的简单抽取
        if "return JSON" in system or "return json" in system.lower():
            return self._extract_json(user)
        # 摘要类调用：截取前若干句
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


class OpenAILLM:
    """OpenAI 兼容端点（OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL）。"""

    def __init__(self) -> None:
        self.base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.key = os.environ.get("OPENAI_API_KEY", "")
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        if not self.key:
            raise RuntimeError("未设置 OPENAI_API_KEY，无法使用真实 LLM。可改用 --llm mock。")
        self.name = f"openai:{self.model}"

    def complete(self, system: str, user: str) -> str:
        import urllib.request

        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        }).encode()
        req = urllib.request.Request(
            f"{self.base}/chat/completions", data=body,
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
            data = json.loads(r.read())
        return data["choices"][0]["message"]["content"]


def get_llm(kind: str = "auto") -> LLM:
    if kind == "mock":
        return MockLLM()
    if kind == "openai" or (kind == "auto" and os.environ.get("OPENAI_API_KEY")):
        return OpenAILLM()
    return MockLLM()
