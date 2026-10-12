"""Background workers (ARQ).

M1 only ships the skeleton: an ARQ worker wired to the same Redis instance so
later milestones can enqueue real jobs (scheduler polling, billing roll-ups)
without new infrastructure.
"""

from app.workers.tasks import ping

__all__ = ["ping"]
