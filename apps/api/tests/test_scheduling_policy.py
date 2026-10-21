"""Queue-ordering policies (F4.3)."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.config import settings
from app.services.scheduling_policy import (
    FifoPolicy,
    PriorityPolicy,
    QueuedJob,
    get_policy,
)


def _job(job_id: int, priority: int, offset_seconds: int) -> QueuedJob:
    return QueuedJob(
        id=job_id,
        user_id=1,
        priority=priority,
        queued_at=datetime(2026, 1, 1, 10, 0, 0) + timedelta(seconds=offset_seconds),
        num_gpus=1,
        num_cpus=1,
        mem_gb=1,
    )


def test_fifo_orders_by_queued_at_then_id() -> None:
    jobs = [_job(2, 0, 10), _job(1, 100, 5), _job(3, 50, 5)]
    assert [job.id for job in FifoPolicy().order(jobs)] == [1, 3, 2]


def test_priority_orders_by_priority_then_fifo() -> None:
    jobs = [_job(1, 0, 0), _job(2, 10, 5), _job(3, 10, 1)]
    assert [job.id for job in PriorityPolicy().order(jobs)] == [3, 2, 1]


def test_policy_factory_uses_config_and_explicit_names(monkeypatch) -> None:
    monkeypatch.setattr(settings, "scheduler_policy", "priority")
    assert get_policy().name == "priority"
    assert get_policy("fifo").name == "fifo"
    assert get_policy("PRIORITY").name == "priority"
