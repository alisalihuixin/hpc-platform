"""Pluggable queue-ordering strategies (F4.3).

The scheduler asks a policy for the order in which queued jobs should be tried;
placement is separate and always "least-loaded node that fits".

Switching is one line: ``SCHEDULER_POLICY=fifo`` (default) or ``priority``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.core.config import settings


@dataclass(frozen=True)
class QueuedJob:
    """The minimum job view a policy needs to make an ordering decision."""

    id: int
    user_id: int
    priority: int
    queued_at: datetime
    num_gpus: int
    num_cpus: int
    mem_gb: int


class SchedulingPolicy(Protocol):
    name: str

    def order(self, jobs: list[QueuedJob]) -> list[QueuedJob]:
        """Return the jobs in the order they should be attempted."""


class FifoPolicy:
    """First come, first served - oldest queue timestamp wins."""

    name = "fifo"

    def order(self, jobs: list[QueuedJob]) -> list[QueuedJob]:
        return sorted(jobs, key=lambda job: (job.queued_at, job.id))


class PriorityPolicy:
    """Highest ``priority`` first, FIFO within the same priority."""

    name = "priority"

    def order(self, jobs: list[QueuedJob]) -> list[QueuedJob]:
        return sorted(jobs, key=lambda job: (-job.priority, job.queued_at, job.id))


_POLICIES: dict[str, SchedulingPolicy] = {
    "fifo": FifoPolicy(),
    "priority": PriorityPolicy(),
}


def get_policy(name: str | None = None) -> SchedulingPolicy:
    """Resolve a policy by name, defaulting to the configured one."""
    key = (name or settings.scheduler_policy).lower()
    try:
        return _POLICIES[key]
    except KeyError as exc:  # pragma: no cover - config is a Literal
        raise ValueError(f"Unknown scheduling policy: {key}") from exc
