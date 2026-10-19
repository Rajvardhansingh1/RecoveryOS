"""The OpenAI-compatible client against a fake HTTP transport (no network)."""

import json

import httpx
import pytest
from pydantic import BaseModel

from app.adapters.llm import (
    LLMAuthError,
    LLMCallBudgetExceeded,
    LLMInvalidOutput,
    LLMNotConfigured,
    LLMRateLimited,
    OpenAICompatibleClient,
    build_llm_client,
    parse_json_object,
)
from app.config import Settings


class Out(BaseModel):
    answer: str


def client_for(handler, models=("m1", "m2"), max_calls=10):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatibleClient(
        provider="openrouter",
        base_url="https://x.test/v1",
        api_key="k",
        models=list(models),
        timeout=5,
        max_output_tokens=100,
        max_calls=max_calls,
        http=http,
    )


def ok(content, finish="stop"):
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": {"total_tokens": 42},
        },
    )


def test_valid_reply_is_parsed_with_metadata():
    c = client_for(lambda req: ok('{"answer": "hi"}'))
    out, meta = c.complete("sys", "user", Out)
    assert out.answer == "hi" and meta.model == "m1" and meta.tokens == 42


def test_request_carries_key_and_never_puts_it_in_the_body():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = req.content.decode()
        return ok('{"answer": "x"}')

    client_for(handler).complete("sys", "user", Out)
    assert seen["auth"] == "Bearer k" and "Bearer" not in seen["body"]
    assert json.loads(seen["body"])["temperature"] == 0


def test_falls_back_to_next_model_on_rate_limit():
    def handler(req):
        model = json.loads(req.content)["model"]
        return httpx.Response(429) if model == "m1" else ok('{"answer": "second"}')

    out, meta = client_for(handler).complete("s", "u", Out)
    assert out.answer == "second" and meta.model == "m2"


def test_auth_error_stops_immediately():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(401)

    with pytest.raises(LLMAuthError):
        client_for(handler).complete("s", "u", Out)
    assert len(calls) == 1


def test_all_models_failing_raises_last_error():
    with pytest.raises(LLMRateLimited):
        client_for(lambda req: httpx.Response(429)).complete("s", "u", Out)


def test_schema_mismatch_and_non_json_are_rejected():
    with pytest.raises(LLMInvalidOutput):
        client_for(lambda req: ok('{"wrong": 1}')).complete("s", "u", Out)
    with pytest.raises(LLMInvalidOutput):
        client_for(lambda req: ok("sure, here you go!")).complete("s", "u", Out)


def test_truncated_reply_is_rejected():
    with pytest.raises(LLMInvalidOutput):
        client_for(lambda req: ok('{"answer": "cut', finish="length")).complete("s", "u", Out)


def test_call_budget_is_enforced():
    c = client_for(lambda req: ok('{"answer": "x"}'), max_calls=1)
    c.complete("s", "u", Out)
    with pytest.raises(LLMCallBudgetExceeded):
        c.complete("s", "u", Out)
    assert c.calls_made == 1


def test_timeout_is_reported_and_falls_over():
    def handler(req):
        if json.loads(req.content)["model"] == "m1":
            raise httpx.ReadTimeout("slow")
        return ok('{"answer": "ok"}')

    out, meta = client_for(handler).complete("s", "u", Out)
    assert meta.model == "m2"


def test_json_fence_is_accepted_but_prose_around_it_is_not():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(LLMInvalidOutput):
        parse_json_object('Here: ```json\n{"a": 1}\n``` done')
    with pytest.raises(LLMInvalidOutput):
        parse_json_object("[1, 2]")


def test_client_requires_key_and_models():
    with pytest.raises(LLMNotConfigured):
        OpenAICompatibleClient(
            provider="groq",
            base_url="u",
            api_key="",
            models=["m"],
            timeout=1,
            max_output_tokens=1,
            max_calls=1,
        )


def test_factory_returns_none_when_unconfigured_and_client_when_configured():
    assert build_llm_client(Settings(_env_file=None, llm_provider="none")) is None
    assert build_llm_client(Settings(_env_file=None, llm_provider="groq", llm_models="m")) is None
    configured = Settings(_env_file=None, llm_provider="groq", llm_models="a,b", GROQ_API_KEY="key")
    assert build_llm_client(configured).provider == "groq"
    assert build_llm_client(configured, max_calls=0) is None
