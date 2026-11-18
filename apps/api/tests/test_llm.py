"""D47: DeepSeek client - JSON mode, retries, circuit breaker, token metering."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.core.llm import (
    ChatModel,
    CircuitBreaker,
    DeepSeekClient,
    LLMRejectedError,
    LLMRequestError,
    LLMResponse,
    LLMResponseFormatError,
    LLMUnavailableError,
)


def _completion(
    *,
    model: str = "deepseek-chat",
    content: str = '{"root_cause": "GPU ECC"}',
    prompt: int = 900,
    completion: int = 120,
    reasoning: str | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return {
        "model": model,
        "choices": [{"index": 0, "message": message}],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    }


def _make_client(
    handler: Any,
    *,
    timeout: float = 30.0,
    max_retries: int = 3,
    breaker: CircuitBreaker | None = None,
) -> DeepSeekClient:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://llm.test",
        timeout=timeout,
    )
    return DeepSeekClient(
        api_key="test-key",
        client=http,
        max_retries=max_retries,
        backoff_seconds=0,
        breaker=breaker or CircuitBreaker(failure_threshold=5, cooldown_seconds=30),
    )


async def test_chat_json_mode_and_token_metering() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        seen["authorization"] = request.headers["authorization"]
        return httpx.Response(200, json=_completion())

    client = _make_client(handler)
    response = await client.complete(
        [{"role": "user", "content": "诊断 gpu-003"}], json_mode=True, temperature=0.0
    )
    await client.aclose()

    payload = seen["payload"]
    assert payload["model"] == "deepseek-chat"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["temperature"] == 0.0
    assert payload["messages"][0]["content"] == "诊断 gpu-003"
    assert seen["authorization"] == "Bearer test-key"

    assert response.attempts == 1
    assert response.prompt_tokens == 900
    assert response.completion_tokens == 120
    assert response.total_tokens == 1020
    assert response.latency_ms >= 0
    assert response.parse_json() == {"root_cause": "GPU ECC"}


async def test_reasoner_switch_and_reasoning_content() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=_completion(model="deepseek-reasoner", reasoning="先查 ECC，再看同交换机"),
        )

    client = _make_client(handler)
    response = await client.complete(
        [{"role": "user", "content": "诊断 gpu-003"}], model=ChatModel.REASONER
    )
    await client.aclose()

    assert seen["payload"]["model"] == "deepseek-reasoner"
    assert "response_format" not in seen["payload"]
    assert response.model == "deepseek-reasoner"
    assert response.reasoning == "先查 ECC，再看同交换机"


async def test_retry_then_success_counts_attempts() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(503)
        return httpx.Response(200, json=_completion())

    client = _make_client(handler)
    response = await client.complete([{"role": "user", "content": "x"}])
    await client.aclose()

    assert calls["n"] == 3
    assert response.attempts == 3
    assert response.parse_json()["root_cause"] == "GPU ECC"


async def test_retries_are_bounded() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500)

    client = _make_client(handler, max_retries=3)
    with pytest.raises(LLMRequestError, match="after 3 attempts"):
        await client.complete([{"role": "user", "content": "x"}])
    await client.aclose()
    assert calls["n"] == 3


async def test_4xx_is_not_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="bad key")

    client = _make_client(handler)
    with pytest.raises(LLMRejectedError, match="401"):
        await client.complete([{"role": "user", "content": "x"}])
    await client.aclose()
    assert calls["n"] == 1


async def test_transport_timeout_is_retried_then_reported() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("simulated upstream timeout", request=request)

    client = _make_client(handler, max_retries=2)
    with pytest.raises(LLMRequestError, match="transport error"):
        await client.complete([{"role": "user", "content": "x"}])
    await client.aclose()
    assert calls["n"] == 2


async def test_circuit_breaker_opens_and_half_opens() -> None:
    now = {"t": 0.0}
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=30, clock=lambda: now["t"])
    upstream = {"ok": False}
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if upstream["ok"]:
            return httpx.Response(200, json=_completion())
        return httpx.Response(503)

    client = _make_client(handler, max_retries=2, breaker=breaker)
    for _ in range(3):
        with pytest.raises(LLMRequestError):
            await client.complete([{"role": "user", "content": "x"}])
    assert breaker.is_open is True
    assert calls["n"] == 6

    # Open circuit: no request leaves the process.
    with pytest.raises(LLMUnavailableError):
        await client.complete([{"role": "user", "content": "x"}])
    assert calls["n"] == 6

    # Cooldown elapsed -> one trial is let through and success closes the circuit.
    now["t"] = 31.0
    upstream["ok"] = True
    response = await client.complete([{"role": "user", "content": "x"}])
    await client.aclose()
    assert response.attempts == 1
    assert breaker.is_open is False


async def test_malformed_payloads_are_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    client = _make_client(handler)
    with pytest.raises(LLMResponseFormatError):
        await client.complete([{"role": "user", "content": "x"}])
    await client.aclose()

    broken = LLMResponse(
        content="not json",
        model="deepseek-chat",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        latency_ms=1,
        attempts=1,
    )
    with pytest.raises(LLMResponseFormatError, match="non-JSON"):
        broken.parse_json()
    array_payload = LLMResponse(
        content="[]",
        model="deepseek-chat",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        latency_ms=1,
        attempts=1,
    )
    with pytest.raises(LLMResponseFormatError, match="object"):
        array_payload.parse_json()
