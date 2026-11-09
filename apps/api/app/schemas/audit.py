from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    table_name: str
    record_id: int
    action: str
    changed_by: str | None
    changed_at: datetime
    before: dict[str, Any] | None
    after: dict[str, Any] | None


class AuditLogList(BaseModel):
    items: list[AuditLogRead]
    total: int
