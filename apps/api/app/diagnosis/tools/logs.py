"""Job-log tool (D44): the last N log lines captured by the node agent."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.diagnosis.tools.base import TOOL_TIMEOUT_SECONDS, ToolResponse, invalid_args, run_tool
from app.diagnosis.tools.node import load_node


class JobLogsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hostname: str = Field(pattern=r"^gpu-\d{3}$", description="e.g. gpu-029")
    #: Upper bound keeps one log dump from blowing the model's context window.
    limit: int = Field(default=50, ge=1, le=200)


async def get_job_logs(
    db: AsyncSession,
    hostname: str,
    *,
    limit: int = 50,
    timeout: float = TOOL_TIMEOUT_SECONDS,
) -> ToolResponse:
    try:
        args = JobLogsArgs(hostname=hostname, limit=limit)
    except ValidationError as exc:
        return invalid_args("get_job_logs", exc)
    return await run_tool(
        "get_job_logs", _logs_payload(db, args.hostname, args.limit), timeout=timeout
    )


async def _logs_payload(db: AsyncSession, hostname: str, limit: int) -> dict[str, Any]:
    node = await load_node(db, hostname)
    lines: list[str] = list((node.tags or {}).get("logs", []))
    return {
        "hostname": node.hostname,
        "lines": lines[-limit:],
        "total_lines": len(lines),
        "truncated": len(lines) > limit,
    }
