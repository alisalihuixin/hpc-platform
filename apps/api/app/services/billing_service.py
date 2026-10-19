"""Billing rules and usage-event corrections (F5.1 / F5.2).

Two invariants:

* **Append-only** - an existing ``usage_events`` row is never updated.  A
  correction is a *new* row with ``reversed=True`` carrying the same amounts;
  reporting subtracts reversal rows from the originals (see
  :func:`net_gpu_hours`).
* **Rate snapshot** - the GPU-hour price in force when the job ran is copied
  into the row, so changing prices later never rewrites history.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.usage_event import UsageEvent

_DEFAULT_RATE_KEY = "default"


def gpu_hours(gpus: int, minutes: int) -> Decimal:
    """GPU count x rounded-up minutes, converted to hours (4 dp)."""
    return (Decimal(gpus * minutes) / Decimal(60)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


def rate_snapshot(gpu_model: str | None) -> dict[str, str]:
    """Freeze the price for a node type into the usage row (F5.2).

    Unknown/absent models fall back to the ``default`` rate.  Decimal values are
    stored as strings so JSON round-trips stay exact.
    """
    rates = settings.gpu_hour_rates
    key = gpu_model if gpu_model in rates else _DEFAULT_RATE_KEY
    rate = Decimal(str(rates.get(key, 0)))
    return {
        "gpu_model": gpu_model or key,
        "rate_key": key,
        "gpu_hour": f"{rate:.4f}",
        "currency": settings.billing_currency,
    }


def net_gpu_hours(events: Iterable[UsageEvent]) -> Decimal:
    """Sum usage, subtracting compensating (``reversed=True``) rows."""
    total = Decimal(0)
    for event in events:
        total += -event.gpu_hours if event.reversed else event.gpu_hours
    return total


async def reverse_job_usage(db: AsyncSession, job_id: int, *, reason: str = "") -> list[UsageEvent]:
    """Append compensating rows for a job's usage, never deleting originals.

    Idempotent: if the job already has reversal rows this is a no-op.  Returns
    the rows created (empty when there was nothing to reverse).
    """
    originals = list(
        (
            await db.scalars(
                select(UsageEvent)
                .where(UsageEvent.job_id == job_id, UsageEvent.reversed.is_(False))
                .order_by(UsageEvent.id)
            )
        ).all()
    )
    if not originals:
        return []

    already_reversed = await db.scalar(
        select(UsageEvent.id).where(UsageEvent.job_id == job_id, UsageEvent.reversed.is_(True))
    )
    if already_reversed is not None:
        return []

    reversals = []
    for original in originals:
        snapshot = dict(original.rate_snapshot)
        if reason:
            snapshot["reversal_reason"] = reason
        reversal = UsageEvent(
            job_id=original.job_id,
            user_id=original.user_id,
            group_id=original.group_id,
            gpus=original.gpus,
            started_at=original.started_at,
            ended_at=original.ended_at,
            duration_min=original.duration_min,
            gpu_hours=original.gpu_hours,
            rate_snapshot=snapshot,
            reversed=True,
        )
        db.add(reversal)
        reversals.append(reversal)

    await db.commit()
    for reversal in reversals:
        await db.refresh(reversal)
    return reversals
