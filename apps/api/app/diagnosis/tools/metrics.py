"""Cluster metrics tool (D45): aggregates over the incident fleet.

The window is capped at 180 minutes - the same horizon the fleet's retention
configuration offers, so the model cannot ask for data that does not exist.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.diagnosis.tools.base import TOOL_TIMEOUT_SECONDS, ToolResponse, invalid_args, run_tool
from app.models.diagnosis import Alert, AlertStatus, IncidentNode


class MetricsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window: int = Field(default=60, ge=1, le=180, description="minutes, <= 180")


async def get_metrics(
    db: AsyncSession, *, window: int = 60, timeout: float = TOOL_TIMEOUT_SECONDS
) -> ToolResponse:
    try:
        args = MetricsArgs(window=window)
    except ValidationError as exc:
        return invalid_args("get_metrics", exc)
    return await run_tool("get_metrics", _metrics_payload(db, args.window), timeout=timeout)


async def _metrics_payload(db: AsyncSession, window: int) -> dict[str, Any]:
    nodes = (await db.scalars(select(IncidentNode))).all()
    by_status = Counter(node.status for node in nodes)
    cards = [card for node in nodes for card in (node.tags or {}).get("gpus", [])]
    open_alerts = await db.scalar(
        select(func.count()).select_from(Alert).where(Alert.status == AlertStatus.OPEN.value)
    )
    return {
        "window_minutes": window,
        "nodes_total": len(nodes),
        "nodes_online": by_status.get("online", 0),
        "nodes_degraded": by_status.get("degraded", 0),
        "nodes_offline": by_status.get("offline", 0),
        "gpus_total": sum(node.gpu_count for node in nodes),
        "gpus_faulty": sum(1 for card in cards if card["status"] != "ok"),
        "alerts_open": int(open_alerts or 0),
    }
