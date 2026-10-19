"""Application settings loaded from environment variables / `.env`."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- App ----
    app_name: str = "HPC Platform API"
    env: str = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"

    # ---- Postgres ----
    postgres_user: str = "hpc"
    postgres_password: str = "hpc"
    postgres_db: str = "hpc"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    # Explicit override wins over the assembled URL (used by tests).
    database_url: str | None = None

    # ---- Redis ----
    redis_url: str = "redis://localhost:6379/0"

    # ---- Auth ----
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    # bcrypt work factor; lowered in tests to keep the suite fast.
    bcrypt_rounds: int = 12

    # ---- Login rate limit (sliding window) ----
    login_rate_limit_max_attempts: int = 5
    login_rate_limit_window_seconds: int = 60

    # ---- Scheduler (F4.3) ----
    # Poll interval for the worker's queued-job scan.
    scheduler_interval_seconds: float = 2.0
    # Probability that a running job fails on a given poll.  Defaults to 0
    # because a non-zero value on a 2s poll would kill jobs unrealistically
    # fast; enable it only for demos of the failure path.
    scheduler_failure_rate: float = Field(default=0.0, ge=0.0, le=1.0)

    # ---- Billing (F5.2) ----
    billing_currency: str = "CNY"
    # Demo prices per GPU-hour keyed by node ``gpu_model``; ``default`` is the
    # fallback.  Override via env as JSON, e.g.
    # GPU_HOUR_RATES={"default": 10, "A100-80G": 30}
    gpu_hour_rates: dict[str, float] = Field(
        default_factory=lambda: {"default": 10.0, "A100-80G": 30.0, "H100": 45.0}
    )

    # ---- CORS ----
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost"]
    )

    # ---- Bootstrap accounts ----
    first_admin_email: str = "admin@hpc.local"
    first_admin_username: str = "admin"
    first_admin_password: str = "admin12345"
    first_user_email: str = "user@hpc.local"
    first_user_username: str = "user"
    first_user_password: str = "user12345"

    @model_validator(mode="after")
    def _assemble_database_url(self) -> Settings:
        if not self.database_url:
            self.database_url = (
                f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_database_url(self) -> str:
        """Sync URL for Alembic offline mode / tooling."""
        assert self.database_url is not None
        return self.database_url.replace("+asyncpg", "+psycopg")

    @property
    def is_production(self) -> bool:
        return self.env.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
