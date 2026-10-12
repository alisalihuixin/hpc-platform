"""Liveness / readiness probes (also reachable through Caddy at ``/health``)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.redis import ping_redis
from app.deps import RedisDep, SessionDep

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness")
async def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name, "env": settings.env}


@router.get("/health/ready", summary="Readiness (checks Postgres + Redis)")
async def ready(db: SessionDep, redis: RedisDep) -> JSONResponse:
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001 - readiness must report, not raise
        db_ok = False

    redis_ok = await ping_redis(redis)
    healthy = db_ok and redis_ok

    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "checks": {"postgres": db_ok, "redis": redis_ok},
        },
    )
