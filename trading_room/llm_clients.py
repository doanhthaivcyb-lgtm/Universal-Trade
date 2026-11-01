from __future__ import annotations

import json
import os
from dataclasses import dataclass

# httpx chỉ import lười bên trong từng client thật, để chế độ --mock
# chạy được mà không cần cài thêm gói nào ngoài pyyaml.
@dataclass
class LLMResponse:
    provider: str
    model: str
    text: str


class LLMClient:
    provider = "base"

    def __init__(self, model: str, temperature: float = 0.2, max_tokens: int = 800):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def complete(self, system: str, user: str) -> LLMResponse:
        raise NotImplementedError


class OpenAIClient(LLMClient):
    provider = "openai"

    def complete(self, system: str, user: str) -> LLMResponse:
        key = os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError("Thiếu OPENAI_API_KEY trong .env")
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        import httpx

        with httpx.Client(timeout=90) as client:
            r = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
            )
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
        return LLMResponse(self.provider, self.model, text)


class GeminiClient(LLMClient):
    provider = "gemini"

    def complete(self, system: str, user: str) -> LLMResponse:
        key = os.getenv("GEMINI_API_KEY", "")
        if not key:
            raise RuntimeError("Thiếu GEMINI_API_KEY trong .env")
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}"
            f":generateContent?key={key}"
        )
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_tokens,
            },
        }
        import httpx

        with httpx.Client(timeout=90) as client:
            r = client.post(url, headers={"Content-Type": "application/json"}, json=payload)
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        return LLMResponse(self.provider, self.model, text)


class ClaudeClient(LLMClient):
    provider = "anthropic"

    def complete(self, system: str, user: str) -> LLMResponse:
        key = os.getenv("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError("Thiếu ANTHROPIC_API_KEY trong .env")
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        import httpx

        with httpx.Client(timeout=90) as client:
            r = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            r.raise_for_status()
            blocks = r.json()["content"]
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        return LLMResponse(self.provider, self.model, text)


class MockClient(LLMClient):
    """Dùng để test cấu trúc mà không gọi API thật, không tốn phí."""

    def __init__(self, model: str = "mock", canned: dict | None = None):
        super().__init__(model)
        self.canned = canned or {}

    def complete(self, system: str, user: str) -> LLMResponse:
        # Mặc định mock trả về HOLD, trừ khi test truyền canned theo role trong system prompt.
        for key, value in self.canned.items():
            if key in system:
                return LLMResponse(self.provider, self.model, json.dumps(value, ensure_ascii=False))
        return LLMResponse(
            self.provider,
            self.model,
            json.dumps(
                {
                    "direction": "HOLD",
                    "confidence": 0.5,
                    "entry": None,
                    "stop_loss": None,
                    "take_profit": None,
                    "reasoning": "Mock: chưa đủ bằng chứng.",
                    "invalidation": "Giá phá vỡ giả định.",
                    "evidence": [],
                },
                ensure_ascii=False,
            ),
        )


def build_client(provider: str, settings: dict, mock: bool = False, canned: dict | None = None) -> LLMClient:
    if mock:
        client = MockClient(canned=canned)
        client.provider = provider
        return client
    model = settings.get("model", "")
    temperature = float(settings.get("temperature", 0.2))
    max_tokens = int(settings.get("max_tokens", 800))
    if provider == "openai":
        return OpenAIClient(model, temperature, max_tokens)
    if provider == "gemini":
        return GeminiClient(model, temperature, max_tokens)
    if provider in ("anthropic", "anthropic_opus"):
        # anthropic_opus là bí danh trong config.yaml để Chief dùng model Opus
        # trong khi Risk dùng Sonnet, cả hai đều gọi API Anthropic như nhau.
        return ClaudeClient(model, temperature, max_tokens)
    raise ValueError(f"Provider chưa hỗ trợ: {provider}")
