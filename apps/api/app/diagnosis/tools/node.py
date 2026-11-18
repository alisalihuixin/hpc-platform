"""Node tools: status and per-GPU state (D44).

Arguments are pydantic models with ``extra='forbid'`` and a hostname pattern, so
a hallucinated ``{"node": "gpu-003", "all": true}`` is rejected at the boundary
instead of silently returning the whole fleet.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.diagnosis.tools.base import (
    TOOL_TIMEOUT_SECONDS,
    ToolNotFoundError,
    ToolResponse,
    invalid_args,
    run_tool,
)
from app.models.diagnosis import IncidentNode


class NodeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hostname: str = Field(pattern=r"^gpu-\d{3}$", description="e.g. gpu-003")


async def load_node(db: AsyncSession, hostname: str) -> IncidentNode:
    """Shared by every tool that needs the node row; unknown host -> not_found."""
    node = await db.scalar(select(IncidentNode).where(IncidentNode.hostname == hostname))
    if node is None:
        raise ToolNotFoundError(f"node {hostname} does not exist in the incident fleet")
    return node


async def get_node_status(
    db: AsyncSession, hostname: str, *, timeout: float = TOOL_TIMEOUT_SECONDS
) -> ToolResponse:
    try:
        args = NodeArgs(hostname=hostname)
    except ValidationError as exc:
        return invalid_args("get_node_status", exc)
    return await run_tool("get_node_status", _status_payload(db, args.hostname), timeout=timeout)


async def _status_payload(db: AsyncSession, hostname: str) -> dict[str, Any]:
    node = await load_node(db, hostname)
    return {
        "hostname": node.hostname,
        "node_model": node.node_model,
        "switch": node.switch,
        "rack": node.rack,
        "gpu_model": node.gpu_model,
        "gpu_count": node.gpu_count,
        "status": node.status,
        "schedulable": node.schedulable,
        "heartbeat_at": node.heartbeat_at.isoformat() if node.heartbeat_at else None,
        "owner": (node.tags or {}).get("owner"),
    }


async def get_node_gpus(
    db: AsyncSession, hostname: str, *, timeout: float = TOOL_TIMEOUT_SECONDS
) -> ToolResponse:
    try:
        args = NodeArgs(hostname=hostname)
    except ValidationError as exc:
        return invalid_args("get_node_gpus", exc)
    return await run_tool("get_node_gpus", _gpus_payload(db, args.hostname), timeout=timeout)


async def _gpus_payload(db: AsyncSession, hostname: str) -> dict[str, Any]:
    node = await load_node(db, hostname)
    cards: list[dict[str, Any]] = list((node.tags or {}).get("gpus", []))
    return {
        "hostname": node.hostname,
        "gpu_model": node.gpu_model,
        "gpu_count": node.gpu_count,
        "cards": cards,
        "faulty_cards": [card["index"] for card in cards if card["status"] != "ok"],
    }
