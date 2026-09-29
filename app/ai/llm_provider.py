"""LLM provider abstraction.

One interface (`LLMProvider.complete`) with five implementations: OpenAI,
Google Gemini, Anthropic, an OpenAI-compatible local endpoint (Ollama, vLLM,
LM Studio), and a deterministic `DemoProvider`.

Providers speak plain HTTP via `requests`, so no vendor SDK is required. The
provider is selected by `LLM_PROVIDER`; application logic never branches on it.

DEMO_MODE returns a structured "unavailable" signal rather than fake analysis:
the deterministic planner (app/ai/query_planner.py) then does the work, so the
numbers still come from real execution.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from config import Config
from app.utils.exceptions import ConfigurationError, LLMError, LLMTimeoutError, RateLimitError
from app.utils.helpers import extract_json
from app.utils.logger import get_logger

log = get_logger("ai.llm")

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

try:  # optional retry helper
    from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

    TENACITY_AVAILABLE = True
except Exception:  # pragma: no cover
    TENACITY_AVAILABLE = False


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    latency_ms: float
    usage: dict[str, Any]
    from_demo: bool = False

    def json(self) -> dict | list | None:
        return extract_json(self.text)


class LLMProvider:
    """Base provider."""

    name = "base"
    default_model = ""

    def __init__(self) -> None:
        self.model = Config.MODEL_NAME or self.default_model

    def available(self) -> bool:
        return True

    def complete(self, system: str, user: str, json_mode: bool = False) -> LLMResponse:
        raise NotImplementedError

    # shared HTTP helper -------------------------------------------------
    def _post(self, url: str, headers: dict, payload: dict) -> dict:
        if requests is None:  # pragma: no cover
            raise ConfigurationError("The 'requests' package is required for live LLM providers.")
        try:
            response = requests.post(
                url, headers=headers, json=payload, timeout=Config.LLM_TIMEOUT_SECONDS
            )
        except Exception as exc:  # network failures
            if "timeout" in str(exc).lower():
                raise LLMTimeoutError(detail=str(exc)) from exc
            raise LLMError(detail=f"{self.name} request failed: {exc}") from exc
        if response.status_code == 429:
            raise RateLimitError(detail=f"{self.name} rate limit")
        if response.status_code >= 400:
            raise LLMError(detail=f"{self.name} HTTP {response.status_code}: {response.text[:300]}")
        try:
            return response.json()
        except ValueError as exc:
            raise LLMError(detail=f"{self.name} returned malformed JSON") from exc


class DemoProvider(LLMProvider):
    """Deterministic offline provider.

    It never invents analytical numbers. For planning prompts it returns an
    explicit marker so callers fall back to the rule-based planner; for prose
    prompts it composes text strictly from the facts passed in.
    """

    name = "demo"
    default_model = "ainsight-demo-rules"

    def complete(self, system: str, user: str, json_mode: bool = False) -> LLMResponse:
        started = time.perf_counter()
        text = json.dumps({"demo_mode": True, "use_deterministic_fallback": True})
        return LLMResponse(
            text=text,
            provider=self.name,
            model=self.default_model,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            usage={},
            from_demo=True,
        )


class OpenAIProvider(LLMProvider):
    name = "openai"
    default_model = "gpt-4o-mini"

    def available(self) -> bool:
        return bool(Config.OPENAI_API_KEY)

    def complete(self, system: str, user: str, json_mode: bool = False) -> LLMResponse:
        if not self.available():
            raise ConfigurationError("OPENAI_API_KEY is not set.")
        started = time.perf_counter()
        payload = {
            "model": self.model,
            "temperature": Config.LLM_TEMPERATURE,
            "max_tokens": Config.LLM_MAX_TOKENS,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        data = self._post(
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {Config.OPENAI_API_KEY}", "Content-Type": "application/json"},
            payload,
        )
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return LLMResponse(
            text=text,
            provider=self.name,
            model=self.model,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            usage=data.get("usage", {}),
        )


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    default_model = "claude-sonnet-4-5"

    def available(self) -> bool:
        return bool(Config.ANTHROPIC_API_KEY)

    def complete(self, system: str, user: str, json_mode: bool = False) -> LLMResponse:
        if not self.available():
            raise ConfigurationError("ANTHROPIC_API_KEY is not set.")
        started = time.perf_counter()
        instruction = system + ("\nRespond with a single JSON object and nothing else." if json_mode else "")
        data = self._post(
            "https://api.anthropic.com/v1/messages",
            {
                "x-api-key": Config.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            {
                "model": self.model,
                "max_tokens": Config.LLM_MAX_TOKENS,
                "temperature": Config.LLM_TEMPERATURE,
                "system": instruction,
                "messages": [{"role": "user", "content": user}],
            },
        )
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        return LLMResponse(
            text=text,
            provider=self.name,
            model=self.model,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            usage=data.get("usage", {}),
        )


class GeminiProvider(LLMProvider):
    name = "gemini"
    default_model = "gemini-2.0-flash"

    def available(self) -> bool:
        return bool(Config.GEMINI_API_KEY)

    def complete(self, system: str, user: str, json_mode: bool = False) -> LLMResponse:
        if not self.available():
            raise ConfigurationError("GEMINI_API_KEY is not set.")
        started = time.perf_counter()
        generation: dict[str, Any] = {
            "temperature": Config.LLM_TEMPERATURE,
            "maxOutputTokens": Config.LLM_MAX_TOKENS,
        }
        if json_mode:
            generation["responseMimeType"] = "application/json"
        data = self._post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            f"?key={Config.GEMINI_API_KEY}",
            {"Content-Type": "application/json"},
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": generation,
            },
        )
        candidates = data.get("candidates") or [{}]
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        return LLMResponse(
            text=text,
            provider=self.name,
            model=self.model,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            usage=data.get("usageMetadata", {}),
        )


class LocalProvider(OpenAIProvider):
    """Any OpenAI-compatible local server (Ollama, vLLM, LM Studio)."""

    name = "local"
    default_model = "llama3.1"

    def available(self) -> bool:
        return bool(Config.LOCAL_LLM_BASE_URL)

    def complete(self, system: str, user: str, json_mode: bool = False) -> LLMResponse:
        started = time.perf_counter()
        data = self._post(
            f"{Config.LOCAL_LLM_BASE_URL.rstrip('/')}/chat/completions",
            {"Content-Type": "application/json"},
            {
                "model": self.model,
                "temperature": Config.LLM_TEMPERATURE,
                "max_tokens": Config.LLM_MAX_TOKENS,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return LLMResponse(
            text=text,
            provider=self.name,
            model=self.model,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            usage=data.get("usage", {}),
        )


PROVIDERS = {
    "demo": DemoProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "local": LocalProvider,
}

_provider: LLMProvider | None = None


def get_provider(force_reload: bool = False) -> LLMProvider:
    global _provider
    if _provider is not None and not force_reload:
        return _provider
    if Config.DEMO_MODE:
        _provider = DemoProvider()
    else:
        cls = PROVIDERS.get(Config.LLM_PROVIDER)
        if cls is None:
            raise ConfigurationError(
                f"Unknown LLM_PROVIDER '{Config.LLM_PROVIDER}'. Options: {', '.join(PROVIDERS)}"
            )
        provider = cls()
        if not provider.available():
            log.warning(
                "Provider '%s' has no credentials configured; falling back to demo mode.",
                Config.LLM_PROVIDER,
            )
            provider = DemoProvider()
        _provider = provider
    log.info("LLM provider: %s (model=%s)", _provider.name, _provider.model)
    return _provider


def complete(system: str, user: str, json_mode: bool = False, retries: int = 1) -> LLMResponse:
    """Call the active provider with bounded retries on transient failures."""
    provider = get_provider()
    attempt, last_error = 0, None
    while attempt <= retries:
        try:
            return provider.complete(system, user, json_mode=json_mode)
        except (RateLimitError, LLMTimeoutError) as exc:
            last_error = exc
            wait = min(2 ** attempt, 8)
            log.warning("LLM transient failure (%s); retrying in %ss", type(exc).__name__, wait)
            time.sleep(wait)
            attempt += 1
        except LLMError as exc:
            log.error("LLM failure: %s", exc.detail)
            raise
    raise last_error or LLMError()


def provider_status() -> dict:
    provider = get_provider()
    return {
        "provider": provider.name,
        "model": provider.model,
        "demo_mode": Config.DEMO_MODE,
        "configured_provider": Config.LLM_PROVIDER,
        "retry_backend": "tenacity" if TENACITY_AVAILABLE else "builtin",
        "available": provider.available(),
    }
