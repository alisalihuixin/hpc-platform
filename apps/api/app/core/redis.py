"""Redis client lifecycle + JWT blacklist helpers.

Two Redis-backed concerns live here (see also ``rate_limit.py``):

* **Login rate limiting** - a sliding window per identity, so brute-force
  attempts are throttled without a hard lockout.
* **JWT blacklist** - on logout we store the token ``jti`` with a TTL equal to
  the token's *remaining* lifetime, so the key self-expires exactly when the
  token would have expired anyway.  No cleanup job needed.

Functions take an explicit ``Redis`` client so callers (and tests) control which
connection is used; only ``get_redis`` owns the shared singleton.
"""

from __future__ import annotations

import time

from redis.asyncio import Redis

from app.core.config import settings

BLACKLIST_PREFIX = "jwt:blacklist:"

_client: Redis | None = None


def get_redis() -> Redis:
    """FastAPI dependency returning the shared async Redis client."""
    global _client
    if _client is None:
        _client = Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def ping_redis(redis: Redis | None = None) -> bool:
    """Readiness probe - never raises."""
    client = redis if redis is not None else get_redis()
    try:
        return bool(await client.ping())
    except Exception:  # noqa: BLE001 - readiness must report, not raise
        return False


# --------------------------------------------------------------------------- #
# JWT blacklist
# --------------------------------------------------------------------------- #
def _blacklist_key(jti: str) -> str:
    return f"{BLACKLIST_PREFIX}{jti}"


async def blacklist_token(redis: Redis, jti: str, expires_at: int) -> None:
    """Blacklist a token id until its natural expiry (TTL = remaining seconds)."""
    ttl = int(expires_at) - int(time.time())
    if ttl > 0:
        await redis.setex(_blacklist_key(jti), ttl, "1")


async def is_token_blacklisted(redis: Redis, jti: str) -> bool:
    return bool(await redis.exists(_blacklist_key(jti)))
