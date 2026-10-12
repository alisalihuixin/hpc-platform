"""ARQ worker settings.  Run with: ``arq app.workers.settings.WorkerSettings``."""

from __future__ import annotations

from arq.connections import RedisSettings

from app.core.config import settings
from app.workers.tasks import ping


class WorkerSettings:
    functions = [ping]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = 300
    keep_result = 3600
