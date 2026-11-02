from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.quota import QuotaRead


class GroupMemberRead(BaseModel):
    """Slim user projection - the group page only needs to identify members."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    # The quota row is created together with the group (1:1).
    gpu_limit: int = Field(default=0, ge=0, le=1_000_000)
    max_nodes_per_job: int = Field(default=1, ge=0, le=4096)
    max_gpus_per_job: int = Field(default=1, ge=0, le=4096)
    member_ids: list[int] = Field(default_factory=list)


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None


class GroupMembersUpdate(BaseModel):
    """Replace the whole membership set (idempotent)."""

    user_ids: list[int] = Field(default_factory=list)


class GroupRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    is_active: bool
    members: list[GroupMemberRead]
    quota: QuotaRead | None
    created_at: datetime
    updated_at: datetime


class GroupList(BaseModel):
    items: list[GroupRead]
    total: int
