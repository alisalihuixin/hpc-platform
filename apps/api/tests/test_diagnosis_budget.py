"""D48: token ledger - accumulation, 40k ceiling, one-shot degradation hook."""

from __future__ import annotations

import pytest

from app.diagnosis.budget import DEFAULT_TOKEN_BUDGET, TokenBudget


def test_defaults_to_the_40k_case_budget() -> None:
    budget = TokenBudget()
    assert budget.limit == DEFAULT_TOKEN_BUDGET == 40_000
    assert budget.used == 0
    assert budget.remaining == 40_000
    assert budget.degraded is False


def test_charges_accumulate_across_calls() -> None:
    budget = TokenBudget(limit=5_000)
    budget.charge(1_000)
    budget.charge(2_500)
    assert budget.used == 3_500
    assert budget.remaining == 1_500
    assert budget.degraded is False


def test_crossing_the_limit_degrades_once() -> None:
    fired: list[TokenBudget] = []
    budget = TokenBudget(limit=100, on_degraded=fired.append)

    budget.charge(60)
    assert budget.degraded is False
    assert fired == []

    budget.charge(50)
    assert budget.used == 110
    assert budget.remaining == 0
    assert budget.degraded is True
    assert fired == [budget]

    budget.charge(10)
    assert budget.used == 120
    assert budget.degraded is True
    assert fired == [budget]


def test_exact_limit_counts_as_degraded() -> None:
    budget = TokenBudget(limit=100)
    budget.charge(100)
    assert budget.degraded is True


def test_snapshot_is_json_safe() -> None:
    budget = TokenBudget(limit=200)
    budget.charge(80)
    assert budget.as_dict() == {
        "limit": 200,
        "used": 80,
        "remaining": 120,
        "degraded": False,
    }


def test_non_positive_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="positive"):
        TokenBudget(limit=0)
