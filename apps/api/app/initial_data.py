"""Bootstrap data: default roles already come from the migration; this creates
the two demo accounts referenced in the README (idempotent).

Usage:  python -m app.initial_data
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import async_session_factory
from app.models.role import Role
from app.models.user import User


async def _ensure_user(
    db,
    *,
    email: str,
    username: str,
    password: str,
    full_name: str,
    role_name: str,
) -> None:
    existing = await db.scalar(select(User).where(User.email == email))
    if existing is not None:
        print(f"= user {email} already exists, skipping")
        return

    role = await db.scalar(select(Role).where(Role.name == role_name))
    if role is None:
        raise RuntimeError(f"role '{role_name}' missing - run migrations first")

    db.add(
        User(
            email=email,
            username=username,
            full_name=full_name,
            hashed_password=hash_password(password),
            roles=[role],
        )
    )
    print(f"+ created {role_name}: {email} / {password}")


async def main() -> None:
    async with async_session_factory() as db:
        await _ensure_user(
            db,
            email=settings.first_admin_email,
            username=settings.first_admin_username,
            password=settings.first_admin_password,
            full_name="Platform Admin",
            role_name="admin",
        )
        await _ensure_user(
            db,
            email=settings.first_user_email,
            username=settings.first_user_username,
            password=settings.first_user_password,
            full_name="Normal User",
            role_name="user",
        )
        await db.commit()
    print("bootstrap complete")


if __name__ == "__main__":
    asyncio.run(main())
