from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.node import NodeStatus


class NodeBase(BaseModel):
    hostname: str = Field(min_length=1, max_length=255, examples=["gpu-01"])
    cpu_cores: int = Field(ge=1, le=4096, examples=[64])
    mem_gb: int = Field(ge=1, examples=[512])
    gpu_model: str | None = Field(default=None, max_length=128, examples=["A100-80G"])
    gpu_total: int = Field(default=0, ge=0, le=4096, examples=[8])
    tags: dict[str, Any] = Field(default_factory=dict, examples=[{"rack": "r1"}])


class NodeCreate(NodeBase):
    """A freshly registered node starts ``online`` with zero GPUs in use."""


class NodeUpdate(BaseModel):
    """Partial update.  Unset fields are left untouched (``exclude_unset``)."""

    hostname: str | None = Field(default=None, min_length=1, max_length=255)
    cpu_cores: int | None = Field(default=None, ge=1, le=4096)
    mem_gb: int | None = Field(default=None, ge=1)
    gpu_model: str | None = Field(default=None, max_length=128)
    gpu_total: int | None = Field(default=None, ge=0, le=4096)
    gpu_used: int | None = Field(default=None, ge=0, le=4096)
    status: NodeStatus | None = None
    tags: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _gpu_used_within_total(self) -> NodeUpdate:
        if (
            self.gpu_used is not None
            and self.gpu_total is not None
            and self.gpu_used > self.gpu_total
        ):
            raise ValueError("gpu_used cannot exceed gpu_total")
        return self


class NodeRead(NodeBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    gpu_used: int
    gpu_free: int
    cpu_used: int
    cpu_free: int
    mem_used: int
    mem_free: int
    status: NodeStatus
    created_at: datetime
    updated_at: datetime


class NodeList(BaseModel):
    items: list[NodeRead]
    total: int
