"""Job state events over Redis Pub/Sub (F4.7).

Every state transition publishes one JSON message on a single channel; SSE
clients subscribe and non-admins filter locally by ``user_id``.  A single
channel keeps publishing (including from the worker process) trivial; the
trade-off is that each API instance sees all events, which is fine at this
scale.

Publishing must never break the write path: failures are logged and swallowed,
because the job state is already committed by the time we publish.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

JOB_EVENTS_CHANNEL = "jobs:events"


async def publish_job_event(redis: Redis | None, *, job_id: int, user_id: int, status: str) -> None:
    """Publish ``job.updated``; a ``None`` client (tests) is a no-op."""
    if redis is None:
        return

    payload = {
        "type": "job.updated",
        "job_id": job_id,
        "user_id": user_id,
        "status": status,
        "at": datetime.now(UTC).isoformat(),
    }
    try:
        await redis.publish(JOB_EVENTS_CHANNEL, json.dumps(payload))
    except Exception:  # noqa: BLE001 - events must not fail the request
        logger.warning("failed to publish job event %s", payload, exc_info=True)


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def job_event_stream(
    redis: Redis,
    *,
    user_id: int,
    is_admin: bool,
    heartbeat_seconds: float = 15.0,
) -> AsyncGenerator[str, None]:
    """Yield SSE frames for the jobs the caller may see.

    ``: ping`` comments are sent whenever the read times out so idle streams
    survive proxies; they are ignored by EventSource.
    """
    pubsub = redis.pubsub()
    await pubsub.subscribe(JOB_EVENTS_CHANNEL)
    try:
        yield ": connected\n\n"
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=heartbeat_seconds
            )
            if message is None:
                yield ": ping\n\n"
                continue

            try:
                payload = json.loads(message["data"])
            except (TypeError, ValueError):
                continue

            if not is_admin and payload.get("user_id") != user_id:
                continue

            yield _sse("job.updated", payload)
    finally:
        await pubsub.unsubscribe(JOB_EVENTS_CHANNEL)
        await pubsub.aclose()
