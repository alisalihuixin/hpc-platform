"""Sliding-window rate limiter backed by a Redis sorted set.

Why a sorted set instead of a plain counter?
--------------------------------------------
A fixed-window counter (``INCR`` + ``EXPIRE``) lets an attacker burst 2x the
limit across a window boundary.  A sorted set stores one member per hit scored
by timestamp; we drop everything older than ``window`` before counting, so the
window *slides* continuously and the count is always "hits in the last N
seconds".  The whole check-and-add runs as a single Lua script, making it
atomic under concurrency (no read-modify-write race between replicas).

Trade-off vs. token bucket: the sliding window is exact and simple to reason
about; a token bucket allows controlled bursts and O(1) memory per key.  For
login throttling, exactness matters more than burst tolerance, so we use the
sorted-set window here.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from redis.asyncio import Redis

# KEYS[1] = window key
# ARGV[1] = now (ms)  ARGV[2] = window (ms)  ARGV[3] = limit  ARGV[4] = member
# returns {allowed(0/1), current_count, retry_after_seconds}
_SLIDING_WINDOW_LUA = """
local key    = KEYS[1]
local now    = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)

if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry_ms = window
  if oldest[2] then
    retry_ms = (tonumber(oldest[2]) + window) - now
  end
  if retry_ms < 0 then retry_ms = 0 end
  return {0, count, math.ceil(retry_ms / 1000)}
end

redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, window)
return {1, count + 1, 0}
"""


@dataclass(slots=True)
class RateLimitResult:
    allowed: bool
    count: int
    retry_after: int

    @property
    def remaining(self) -> int:
        return max(0, self.count)


async def check_rate_limit(
    redis: Redis,
    key: str,
    limit: int,
    window_seconds: int,
) -> RateLimitResult:
    """Register a hit for ``key`` and report whether it is still within ``limit``."""
    now_ms = int(time.time() * 1000)
    window_ms = int(window_seconds * 1000)
    member = f"{now_ms}-{uuid.uuid4().hex}"

    allowed, count, retry_after = await redis.eval(
        _SLIDING_WINDOW_LUA,
        1,
        key,
        now_ms,
        window_ms,
        limit,
        member,
    )
    return RateLimitResult(allowed=bool(allowed), count=int(count), retry_after=int(retry_after))


async def reset_rate_limit(redis: Redis, key: str) -> None:
    """Clear a window (e.g. after a successful login)."""
    await redis.delete(key)
