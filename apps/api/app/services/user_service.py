"""User CRUD use-cases (admin-facing in M1)."""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.core.security import hash_password
from app.models.role import Role
from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate


async def get_by_id(db: AsyncSession, user_id: int) -> User | None:
    return await db.get(User, user_id)


async def list_users(db: AsyncSession, *, skip: int = 0, limit: int = 50) -> tuple[list[User], int]:
    total = await db.scalar(select(func.count()).select_from(User)) or 0
    rows = await db.scalars(select(User).order_by(User.id).offset(skip).limit(limit))
    return list(rows.all()), int(total)


async def _resolve_roles(db: AsyncSession, names: list[str]) -> list[Role]:
    if not names:
        return []
    roles = list((await db.scalars(select(Role).where(Role.name.in_(names)))).all())
    missing = set(names) - {role.name for role in roles}
    if missing:
        raise NotFoundError(f"Unknown role(s): {', '.join(sorted(missing))}")
    return roles


async def create_user(db: AsyncSession, payload: UserCreate) -> User:
    existing = await db.scalar(
        select(User).where(or_(User.email == payload.email, User.username == payload.username))
    )
    if existing is not None:
        raise ConflictError("Email or username is already registered")

    user = User(
        email=payload.email,
        username=payload.username,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        roles=await _resolve_roles(db, payload.roles),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def update_user(db: AsyncSession, user_id: int, payload: UserUpdate) -> User:
    user = await get_by_id(db, user_id)
    if user is None:
        raise NotFoundError("User not found")

    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.password:
        user.hashed_password = hash_password(payload.password)
    if payload.roles is not None:
        user.roles = await _resolve_roles(db, payload.roles)

    await db.commit()
    await db.refresh(user)
    return user
