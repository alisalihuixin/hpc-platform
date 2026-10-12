"""Authentication + RBAC dependencies.

``get_token_payload`` validates the bearer token (signature, expiry, token type,
blacklist) and hands the decoded claims to callers.  ``get_current_user`` builds
on it to load the ORM user.  ``require_roles`` is the RBAC gate used by routers:

    @router.get("/admin-only", dependencies=[Depends(require_roles("admin"))])
"""

from __future__ import annotations

from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.redis import is_token_blacklisted
from app.core.security import decode_token
from app.deps.db import RedisDep, SessionDep
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_token_payload(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    redis: RedisDep,
) -> dict[str, Any]:
    if credentials is None or not credentials.credentials:
        raise _unauthorized("Not authenticated")

    try:
        payload = decode_token(credentials.credentials)
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("Token has expired") from exc
    except jwt.PyJWTError as exc:
        raise _unauthorized("Could not validate credentials") from exc

    if payload.get("type") != "access":
        raise _unauthorized("Invalid token type")

    jti = payload.get("jti")
    if jti and await is_token_blacklisted(redis, jti):
        raise _unauthorized("Token has been revoked")

    if "sub" not in payload:
        raise _unauthorized("Malformed token")
    return payload


TokenPayload = Annotated[dict[str, Any], Depends(get_token_payload)]


async def get_current_user(payload: TokenPayload, db: SessionDep) -> User:
    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError) as exc:
        raise _unauthorized("Malformed token subject") from exc

    user = await db.get(User, user_id)
    if user is None:
        raise _unauthorized("User no longer exists")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is disabled")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*required: str):
    """Dependency factory enforcing that the caller holds at least one role."""

    async def _checker(user: CurrentUser) -> User:
        if not user.has_role(*required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of the roles: {', '.join(required)}",
            )
        return user

    return _checker


AdminUser = Annotated[User, Depends(require_roles("admin"))]
