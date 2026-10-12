"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.deps import CurrentUser, RedisDep, SessionDep, TokenPayload
from app.schemas.common import Message
from app.schemas.token import LoginRequest, LogoutRequest, RefreshRequest, Token
from app.schemas.user import UserRead
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _to_token(pair: auth_service.TokenPair) -> Token:
    return Token(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        token_type=pair.token_type,
        expires_in=pair.expires_in,
    )


@router.post("/login", response_model=Token, summary="Log in (rate limited)")
async def login(
    request: Request,
    payload: LoginRequest,
    db: SessionDep,
    redis: RedisDep,
) -> Token:
    client_ip = request.client.host if request.client else "unknown"
    pair = await auth_service.login(
        db,
        redis,
        identifier=payload.username,
        password=payload.password,
        client_ip=client_ip,
    )
    return _to_token(pair)


@router.post("/refresh", response_model=Token, summary="Rotate a refresh token")
async def refresh(payload: RefreshRequest, db: SessionDep, redis: RedisDep) -> Token:
    pair = await auth_service.refresh(db, redis, payload.refresh_token)
    return _to_token(pair)


@router.post("/logout", response_model=Message, summary="Revoke the current tokens")
async def logout(
    token_payload: TokenPayload,
    user: CurrentUser,
    redis: RedisDep,
    payload: LogoutRequest | None = None,
) -> Message:
    await auth_service.logout(redis, token_payload, payload.refresh_token if payload else None)
    return Message(detail="Logged out")


@router.get("/me", response_model=UserRead, summary="Current user profile")
async def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)
