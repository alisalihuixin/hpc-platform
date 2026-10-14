"""Bootstrap data: default roles already come from the migration; this creates
the two demo accounts referenced in the README plus a default group with its
quota (idempotent).

Usage:  python -m app.initial_data
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import async_session_factory
from app.models.group import Group
from app.models.quota import Quota
from app.models.role import Role
from app.models.user import User

DEFAULT_GROUP_NAME = "default"


async def _ensure_user(
    db: AsyncSession,
    *,
    email: str,
    username: str,
    password: str,
    full_name: str,
    role_name: str,
) -> User:
    existing = await db.scalar(select(User).where(User.email == email))
    if existing is not None:
        print(f"= user {email} already exists, skipping")
        return existing

    role = await db.scalar(select(Role).where(Role.name == role_name))
    if role is None:
        raise RuntimeError(f"role '{role_name}' missing - run migrations first")

    user = User(
        email=email,
        username=username,
        full_name=full_name,
        hashed_password=hash_password(password),
        roles=[role],
    )
    db.add(user)
    print(f"+ created {role_name}: {email} / {password}")
    return user


async def _ensure_group(
    db: AsyncSession, *, name: str, members: list[User], gpu_limit: int
) -> Group:
    group = await db.scalar(select(Group).where(Group.name == name))
    if group is None:
        group = Group(name=name, description="Default group for demo accounts")
        db.add(group)
        await db.flush()
        db.add(
            Quota(group_id=group.id, gpu_limit=gpu_limit, max_nodes_per_job=2, max_gpus_per_job=4)
        )
        print(f"+ created group {name} (gpu_limit={gpu_limit})")

    member_ids = {member.id for member in group.members}
    for member in members:
        if member.id not in member_ids:
            group.members.append(member)
            print(f"+ added {member.email} to {name}")
    return group


async def main() -> None:
    async with async_session_factory() as db:
        admin = await _ensure_user(
            db,
            email=settings.first_admin_email,
            username=settings.first_admin_username,
            password=settings.first_admin_password,
            full_name="Platform Admin",
            role_name="admin",
        )
        user = await _ensure_user(
            db,
            email=settings.first_user_email,
            username=settings.first_user_username,
            password=settings.first_user_password,
            full_name="Normal User",
            role_name="user",
        )
        # Both demo accounts live in the default group so quota checks have
        # something to spend (F3).
        await db.flush()
        await _ensure_group(db, name=DEFAULT_GROUP_NAME, members=[admin, user], gpu_limit=1000)
        await db.commit()
    print("bootstrap complete")


if __name__ == "__main__":
    asyncio.run(main())
