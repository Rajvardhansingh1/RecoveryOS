"""LLM adapter for any OpenAI-compatible chat API (OpenRouter, Groq).

The client returns schema-validated objects plus metadata, or raises LLMError. It never
returns unvalidated text. Model fallback, timeouts and retries are bounded here; the decision
to fall back to deterministic code belongs to the caller (core.steps).
"""

import json
import re
import secrets
from dataclasses import dataclass
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import Settings

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    reason = "llm_error"


class LLMNotConfigured(LLMError):
    reason = "llm_not_configured"


class LLMCallBudgetExceeded(LLMError):
    reason = "llm_call_budget_exceeded"


class LLMTimeout(LLMError):
    reason = "llm_timeout"


class LLMRateLimited(LLMError):
    reason = "llm_rate_limited"


class LLMAuthError(LLMError):
    reason = "llm_auth_error"


class LLMInvalidOutput(LLMError):
    reason = "llm_invalid_output"


class LLMUnavailable(LLMError):
    reason = "llm_unavailable"


@dataclass
class LLMMeta:
    provider: str
    model: str
    calls: int = 1
    tokens: int = 0


class LLMClient(Protocol):
    provider: str
    calls_made: int

    def complete(self, system: str, user: str, schema: type[T]) -> tuple[T, LLMMeta]: ...


def fence(text: str) -> tuple[str, str]:
    """Wrap untrusted text in a random delimiter so it cannot close its own fence."""
    token = secrets.token_hex(6)
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return token, f"<<DATA-{token}\n{cleaned}\nDATA-{token}>>"


def parse_json_object(raw: str) -> dict:
    """Accept a bare JSON object, optionally inside a ```json fence. Nothing else."""
    text = raw.strip()
    m = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    if m:
        text = m.group(1)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMInvalidOutput("reply is not valid JSON") from exc
    if not isinstance(value, dict):
        raise LLMInvalidOutput("reply is not a JSON object")
    return value


class OpenAICompatibleClient:
    """Chat-completions client with an ordered model list.

    A model that times out, is rate limited, is unavailable or returns unusable output hands
    over to the next model. An authentication error stops immediately.
    """

    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        api_key: str,
        models: list[str],
        timeout: float,
        max_output_tokens: int,
        max_calls: int,
        http: httpx.Client | None = None,
    ):
        if not api_key or not models:
            raise LLMNotConfigured("missing API key or model list")
        self.provider = provider
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._key = api_key
        self._models = models
        self._timeout = timeout
        self._max_tokens = max_output_tokens
        self._max_calls = max_calls
        self._calls = 0
        self._http = http or httpx.Client(timeout=timeout)

    @property
    def calls_made(self) -> int:
        return self._calls

    def _call(self, model: str, system: str, user: str) -> tuple[str, int]:
        if self._calls >= self._max_calls:
            raise LLMCallBudgetExceeded("per-run LLM call cap reached")
        self._calls += 1
        try:
            resp = self._http.post(
                self._url,
                headers={"Authorization": f"Bearer {self._key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0,
                    "max_tokens": self._max_tokens,
                    "response_format": {"type": "json_object"},
                },
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeout("provider timed out") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailable("provider unreachable") from exc
        if resp.status_code in (401, 403):
            raise LLMAuthError("provider rejected the API key")
        if resp.status_code == 429:
            raise LLMRateLimited("provider rate limit")
        if resp.status_code >= 500 or resp.status_code in (402, 404):
            raise LLMUnavailable(f"provider status {resp.status_code}")
        if resp.status_code >= 400:
            raise LLMInvalidOutput(f"provider status {resp.status_code}")
        try:
            body = resp.json()
            choice = body["choices"][0]
            content = choice["message"]["content"] or ""
            tokens = int((body.get("usage") or {}).get("total_tokens") or 0)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMInvalidOutput("unexpected response shape") from exc
        if choice.get("finish_reason") == "length":
            raise LLMInvalidOutput("reply cut off by token limit")
        return content, tokens

    def complete(self, system: str, user: str, schema: type[T]) -> tuple[T, LLMMeta]:
        last: LLMError = LLMUnavailable("no model tried")
        for model in self._models:
            try:
                content, tokens = self._call(model, system, user)
                obj = schema.model_validate(parse_json_object(content))
                return obj, LLMMeta(self.provider, model, calls=self._calls, tokens=tokens)
            except LLMAuthError:
                raise
            except LLMCallBudgetExceeded:
                raise
            except ValidationError as exc:
                last = LLMInvalidOutput(f"schema validation failed: {len(exc.errors())} errors")
            except LLMError as exc:
                last = exc
        raise last


def build_llm_client(settings: Settings, max_calls: int | None = None) -> LLMClient | None:
    """Return a client, or None when no provider is configured (steps then use the fallback)."""
    if settings.llm_provider == "none":
        return None
    cap = settings.llm_max_calls_per_run if max_calls is None else max_calls
    if cap <= 0:
        return None
    if settings.llm_provider == "openrouter":
        key, base = settings.openrouter_api_key, settings.openrouter_base_url
    else:
        key, base = settings.groq_api_key, settings.groq_base_url
    try:
        return OpenAICompatibleClient(
            provider=settings.llm_provider,
            base_url=base,
            api_key=key,
            models=settings.model_list,
            timeout=settings.llm_timeout_seconds,
            max_output_tokens=settings.llm_max_output_tokens,
            max_calls=cap,
        )
    except LLMNotConfigured:
        return None
