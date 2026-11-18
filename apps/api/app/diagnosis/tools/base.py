"""Shared tool contract: structured results, one timeout, no leaking exceptions.

Every tool returns :class:`ToolResponse`; a missing node or a slow query becomes
``ok=False`` plus a machine-readable code, never a traceback.  The graph stores
these dicts in ``diagnosis_steps.result`` and the model reads them back, so the
shape is part of the prompt contract.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

#: One budget for every tool.  A stuck SSH/DB call must not stall the whole
#: investigation - the agent can retry a different tool instead.
TOOL_TIMEOUT_SECONDS = 10.0


class ToolErrorCode(StrEnum):
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    INVALID_ARGS = "invalid_args"


class ToolNotFoundError(LookupError):
    """Raised by a tool's data loader; converted into a structured error."""


@dataclass(frozen=True, slots=True)
class ToolError:
    code: ToolErrorCode
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code.value, "message": self.message}


@dataclass(frozen=True, slots=True)
class ToolResponse:
    tool: str
    ok: bool
    latency_ms: int
    data: dict[str, Any] | None = None
    error: ToolError | None = None

    @classmethod
    def failure(
        cls, tool: str, code: ToolErrorCode, message: str, latency_ms: int = 0
    ) -> ToolResponse:
        return cls(tool=tool, ok=False, latency_ms=latency_ms, error=ToolError(code, message))

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "data": self.data,
            "error": self.error.as_dict() if self.error else None,
        }


def elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def invalid_args(tool: str, exc: ValidationError) -> ToolResponse:
    """Turn a pydantic rejection into a structured error for the model."""
    first = exc.errors()[0]
    field = ".".join(str(part) for part in first["loc"])
    return ToolResponse.failure(tool, ToolErrorCode.INVALID_ARGS, f"{field}: {first['msg']}")


async def run_tool(
    tool: str, work: Awaitable[dict[str, Any]], *, timeout: float = TOOL_TIMEOUT_SECONDS
) -> ToolResponse:
    """Await ``work`` under the shared timeout, mapping failures to codes."""
    started = time.perf_counter()
    try:
        data = await asyncio.wait_for(work, timeout=timeout)
    except TimeoutError:
        return ToolResponse.failure(
            tool, ToolErrorCode.TIMEOUT, f"{tool} exceeded {timeout:.1f}s", elapsed_ms(started)
        )
    except ToolNotFoundError as exc:
        return ToolResponse.failure(tool, ToolErrorCode.NOT_FOUND, str(exc), elapsed_ms(started))
    return ToolResponse(tool=tool, ok=True, latency_ms=elapsed_ms(started), data=data)
