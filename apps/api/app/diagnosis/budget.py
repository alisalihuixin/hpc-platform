"""Per-case token budget (D48).

One diagnosis run gets a fixed allowance (40k by default).  The graph charges
every LLM response into the ledger; when the total crosses the limit the budget
fires its degradation hook exactly once, which is the signal to stop looping and
write a rule-based conclusion (D53).  Keeping this ledger free of DB/LLM imports
means it can be unit-tested as plain arithmetic.
"""

from __future__ import annotations

from collections.abc import Callable

from app.core.config import settings

#: Fallback when the caller does not pass a limit explicitly.
DEFAULT_TOKEN_BUDGET = 40_000


class TokenBudget:
    """Running total for one diagnosis run; ``degraded`` latches at the limit."""

    def __init__(
        self,
        *,
        limit: int | None = None,
        on_degraded: Callable[[TokenBudget], None] | None = None,
    ) -> None:
        if limit is not None and limit <= 0:
            raise ValueError("token budget must be positive")
        self._limit = limit if limit is not None else settings.diagnosis_token_budget
        self._used = 0
        self._degraded = False
        self._on_degraded = on_degraded

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return max(0, self._limit - self._used)

    @property
    def degraded(self) -> bool:
        return self._degraded

    def charge(self, tokens: int) -> None:
        """Add one response's tokens; fires the hook on first crossing only."""
        self._used += tokens
        if not self._degraded and self._used >= self._limit:
            self._degraded = True
            if self._on_degraded is not None:
                self._on_degraded(self)

    def as_dict(self) -> dict[str, int | bool]:
        """Snapshot persisted on ``diagnosis_runs`` / shown in the report."""
        return {
            "limit": self._limit,
            "used": self._used,
            "remaining": self.remaining,
            "degraded": self._degraded,
        }
