"""Placeholder task used to prove the worker wiring end to end."""

from __future__ import annotations

from typing import Any


async def ping(ctx: dict[str, Any], message: str = "pong") -> str:
    return f"{message} from worker {ctx.get('job_id', 'unknown')}"
