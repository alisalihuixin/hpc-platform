"""Shared FastAPI dependencies."""

from app.deps.auth import (
    AdminUser,
    CurrentUser,
    TokenPayload,
    get_current_user,
    get_token_payload,
    require_roles,
)
from app.deps.db import RedisDep, SessionDep
from app.deps.diagnosis import DiagnosisLLMDep, get_completer

__all__ = [
    "SessionDep",
    "RedisDep",
    "DiagnosisLLMDep",
    "CurrentUser",
    "AdminUser",
    "TokenPayload",
    "get_completer",
    "get_current_user",
    "get_token_payload",
    "require_roles",
]
