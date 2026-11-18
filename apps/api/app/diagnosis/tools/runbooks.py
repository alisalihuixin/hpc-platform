"""Runbook lookup tool (D45): ILIKE over title/body/tags until M8 adds RAG."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.diagnosis.tools.base import TOOL_TIMEOUT_SECONDS, ToolResponse, invalid_args, run_tool
from app.models.diagnosis import Runbook


class RunbookQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(default=None, min_length=2, max_length=100)
    #: Comma-separated keywords; every token must appear in ``runbooks.tags``.
    tags: str | None = Field(default=None, min_length=2, max_length=100)
    limit: int = Field(default=5, ge=1, le=10)


async def search_runbooks(
    db: AsyncSession,
    *,
    query: str | None = None,
    tags: str | None = None,
    limit: int = 5,
    timeout: float = TOOL_TIMEOUT_SECONDS,
) -> ToolResponse:
    try:
        args = RunbookQuery(query=query, tags=tags, limit=limit)
    except ValidationError as exc:
        return invalid_args("search_runbooks", exc)
    return await run_tool("search_runbooks", _search_payload(db, args), timeout=timeout)


async def _search_payload(db: AsyncSession, args: RunbookQuery) -> dict[str, Any]:
    statement = select(Runbook).order_by(Runbook.id).limit(args.limit)
    if args.tags:
        for token in (part.strip() for part in args.tags.split(",")):
            if token:
                statement = statement.where(Runbook.tags.ilike(f"%{token}%"))
    if args.query:
        pattern = f"%{args.query}%"
        statement = statement.where(or_(Runbook.title.ilike(pattern), Runbook.body.ilike(pattern)))
    rows = (await db.scalars(statement)).all()
    return {
        "count": len(rows),
        "runbooks": [
            {
                "id": row.id,
                "title": row.title,
                "tags": row.tags,
                "excerpt": row.body[:160],
            }
            for row in rows
        ],
    }
