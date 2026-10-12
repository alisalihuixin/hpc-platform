"""Authentication use-cases: login (rate limited), refresh (rotating), logout.

Token lifecycle
---------------
* **login** -> issue an access + refresh pair.  The sliding-window limiter is
  keyed on ``ip:identifier`` and cleared on success.
* **refresh** -> verify the refresh token, then *rotate*: the presented token is
  blacklisted before a new pair is issued, so a stolen refresh token is usable
  at most once.
* **logout** -> blacklist the access token's ``jti`` (and the refresh token's if
  provided).  Both keys carry a TTL equal to the token's remaining lifetime, so
  Redis reclaims them automatically.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from redis.asyncio import Redis
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AuthenticationError, PermissionDeniedError, RateLimitError
from app.core.rate_limit import check_rate_limit, reset_rate_limit
from app.core.redis import blacklist_token, is_token_blacklisted
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import User


@dataclass(slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "bearer"


@lru_cache
def _dummy_hash() -> str:
    """A real bcrypt hash used to equalise timing for unknown users."""
    return hash_password("timing-equalisation-placeholder")


def login_attempt_key(client_ip: str, identifier: str) -> str:
    return f"ratelimit:login:{client_ip}:{identifier.lower()}"


def issue_tokens(user: User) -> TokenPair:
    access_token, _, _ = create_access_token(user.id, roles=sorted(user.role_names))
    refresh_token, _, _ = create_refresh_token(user.id)
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


async def authenticate(db: AsyncSession, identifier: str, password: str) -> User:
    """Resolve ``identifier`` (email *or* username) and verify the password."""
    stmt = select(User).where(or_(User.email == identifier, User.username == identifier))
    user = await db.scalar(stmt)

    if user is None:
        # Hash anyway so a missing user isn't distinguishable by response time.
        verify_password(password, _dummy_hash())
        raise AuthenticationError("Invalid credentials")

    if not verify_password(password, user.hashed_password):
        raise AuthenticationError("Invalid credentials")

    if not user.is_active:
        raise PermissionDeniedError("User is disabled")

    return user


async def login(
    db: AsyncSession,
    redis: Redis,
    *,
    identifier: str,
    password: str,
    client_ip: str,
) -> TokenPair:
    key = login_attempt_key(client_ip, identifier)
    result = await check_rate_limit(
        redis,
        key,
        limit=settings.login_rate_limit_max_attempts,
        window_seconds=settings.login_rate_limit_window_seconds,
    )
    if not result.allowed:
        raise RateLimitError(
            "Too many login attempts. Please try again later.",
            retry_after=result.retry_after,
        )

    user = await authenticate(db, identifier, password)
    await reset_rate_limit(redis, key)
    return issue_tokens(user)


async def refresh(db: AsyncSession, redis: Redis, refresh_token: str) -> TokenPair:
    try:
        payload = decode_token(refresh_token)
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid refresh token") from exc

    if payload.get("type") != "refresh":
        raise AuthenticationError("Invalid token type")

    jti = payload.get("jti")
    if jti and await is_token_blacklisted(redis, jti):
        raise AuthenticationError("Refresh token has been revoked")

    user = await db.get(User, int(payload.get("sub", 0) or 0))
    if user is None or not user.is_active:
        raise AuthenticationError("User not found or disabled")

    # Rotate: burn the presented refresh token before issuing a new pair.
    if jti:
        await blacklist_token(redis, str(jti), int(payload.get("exp", 0)))

    return issue_tokens(user)


async def logout(
    redis: Redis, payload: Mapping[str, Any], refresh_token: str | None = None
) -> None:
    """Revoke the current access token, and optionally the refresh token."""
    jti = payload.get("jti")
    if jti:
        await blacklist_token(redis, str(jti), int(payload.get("exp", 0)))

    if refresh_token:
        try:
            refresh_payload = decode_token(refresh_token)
        except jwt.PyJWTError:
            return
        refresh_jti = refresh_payload.get("jti")
        if refresh_jti:
            await blacklist_token(redis, str(refresh_jti), int(refresh_payload.get("exp", 0)))
