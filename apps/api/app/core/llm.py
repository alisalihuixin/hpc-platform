"""DeepSeek client for the diagnosis agent (D47).

One class serves both models: ``deepseek-chat`` for cheap structured steps and
``deepseek-reasoner`` behind the same call, so D58 can A/B them without touching
graph code.

Three guards protect the token budget: a per-request timeout, bounded retries
with exponential backoff, and a circuit breaker that fails fast once upstream is
down - a dead API must not burn the 40k-token allowance on retries.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Any, Literal, TypedDict

import httpx

from app.core.config import settings


class ChatModel(StrEnum):
    CHAT = "deepseek-chat"
    REASONER = "deepseek-reasoner"


class Message(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


#: Statuses worth retrying: rate limits and transient upstream failures.
_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})


class LLMError(RuntimeError):
    """Base class for every failure the client reports to the caller."""


class LLMRequestError(LLMError):
    """Retries exhausted (timeout, transport error, persistent 5xx)."""


class LLMRejectedError(LLMError):
    """4xx - retrying the same payload can never succeed."""


class LLMUnavailableError(LLMError):
    """The circuit breaker is open; no request was sent."""


class LLMResponseFormatError(LLMError):
    """The payload is not the chat-completion shape we expect."""


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    attempts: int
    #: Only set by ``deepseek-reasoner``.
    reasoning: str | None = None

    def parse_json(self) -> dict[str, Any]:
        """Decode JSON-mode content; raising beats letting bad JSON reach the graph."""
        try:
            payload = json.loads(self.content)
        except json.JSONDecodeError as exc:
            raise LLMResponseFormatError(f"{self.model} returned non-JSON content") from exc
        if not isinstance(payload, dict):
            raise LLMResponseFormatError(f"{self.model} JSON mode must return an object")
        return payload


class CircuitBreaker:
    """Consecutive-failure breaker with a half-open trial after the cooldown."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        return self._failures >= self._threshold and not self._cooldown_elapsed()

    def _cooldown_elapsed(self) -> bool:
        return self._opened_at is not None and self._clock() - self._opened_at >= self._cooldown

    def allow(self) -> bool:
        if self._failures < self._threshold:
            return True
        # Half-open: let one trial through once the cooldown passed.
        return self._cooldown_elapsed()

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = self._clock()


class DeepSeekClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        backoff_seconds: float | None = None,
        breaker: CircuitBreaker | None = None,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else settings.deepseek_api_key
        self._timeout = timeout if timeout is not None else settings.llm_timeout_seconds
        self._max_retries = max_retries if max_retries is not None else settings.llm_max_retries
        self._backoff_seconds = (
            backoff_seconds if backoff_seconds is not None else settings.llm_retry_backoff_seconds
        )
        self._breaker = breaker or CircuitBreaker(
            failure_threshold=settings.llm_circuit_failure_threshold,
            cooldown_seconds=settings.llm_circuit_cooldown_seconds,
        )
        self._owns_client = client is None
        # Sent per request so an injected client (tests, SSE) inherits the key too.
        self._headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        self._client = client or httpx.AsyncClient(
            base_url=base_url or settings.deepseek_base_url,
            timeout=self._timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: ChatModel = ChatModel.CHAT,
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        if not self._breaker.allow():
            raise LLMUnavailableError("DeepSeek circuit is open; call skipped")

        payload: dict[str, Any] = {
            "model": model.value,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                body = await self._post(payload)
            except LLMRejectedError:
                raise
            except LLMRequestError as exc:
                last_error = exc
                if attempt == self._max_retries:
                    break
                await asyncio.sleep(self._backoff_seconds * 2 ** (attempt - 1))
                continue
            self._breaker.record_success()
            return self._parse(body, model=model.value, attempts=attempt, started=started)

        self._breaker.record_failure()
        raise LLMRequestError(
            f"DeepSeek call failed after {self._max_retries} attempts: {last_error}"
        )

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(
                "/chat/completions", json=payload, headers=self._headers
            )
        except httpx.HTTPError as exc:
            raise LLMRequestError(f"transport error: {exc}") from exc

        if response.status_code in _RETRYABLE_STATUS:
            raise LLMRequestError(f"upstream HTTP {response.status_code}")
        if response.status_code >= 400:
            raise LLMRejectedError(
                f"upstream rejected the request: HTTP {response.status_code} {response.text[:200]}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise LLMRequestError("upstream returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise LLMRequestError("upstream returned a non-object payload")
        return body

    @staticmethod
    def _parse(body: dict[str, Any], *, model: str, attempts: int, started: float) -> LLMResponse:
        try:
            message = body["choices"][0]["message"]
            content = message.get("content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseFormatError(f"unexpected DeepSeek payload: {exc}") from exc

        usage = body.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        return LLMResponse(
            content=content,
            model=body.get("model") or model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=int(usage.get("total_tokens") or prompt_tokens + completion_tokens),
            latency_ms=int((time.perf_counter() - started) * 1000),
            attempts=attempts,
            reasoning=message.get("reasoning_content"),
        )


@lru_cache
def get_llm_client() -> DeepSeekClient:
    """Process-wide client; tests construct their own with a MockTransport."""
    return DeepSeekClient()
