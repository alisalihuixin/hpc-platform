"""D44: node/log tools - happy path, not found, invalid args, timeout."""

from __future__ import annotations

import asyncio

from app.diagnosis.data.mock import seed_diagnosis_data
from app.diagnosis.tools import get_job_logs, get_node_gpus, get_node_status
from app.diagnosis.tools import node as node_tools


async def _seeded(session_factory) -> None:
    async with session_factory() as db:
        await seed_diagnosis_data(db)
        await db.commit()


async def test_node_status_reports_health_and_topology(session_factory) -> None:
    await _seeded(session_factory)
    async with session_factory() as db:
        response = await get_node_status(db, "gpu-003")
        assert response.ok and response.error is None
        assert response.latency_ms >= 0
        assert response.data is not None
        data = response.data
        assert data["hostname"] == "gpu-003"
        assert data["switch"] == "sw-01"
        assert data["rack"] == "rack-a"
        assert data["status"] == "online"
        assert data["schedulable"] is True
        assert data["heartbeat_at"].startswith("2026-11-17T08:59")

        cordoned = await get_node_status(db, "gpu-009")
        assert cordoned.ok and cordoned.data is not None
        assert cordoned.data["schedulable"] is False
        assert cordoned.data["status"] == "online"


async def test_node_gpus_exposes_per_card_state(session_factory) -> None:
    await _seeded(session_factory)
    async with session_factory() as db:
        response = await get_node_gpus(db, "gpu-003")
        assert response.ok and response.data is not None
        assert response.data["gpu_count"] == 8
        assert len(response.data["cards"]) == 8
        assert response.data["faulty_cards"] == [2]
        assert response.data["cards"][2]["ecc_errors"] == 12
        assert response.data["cards"][2]["status"] == "ecc_error"

        offline = await get_node_gpus(db, "gpu-029")
        assert offline.data is not None
        assert offline.data["faulty_cards"] == list(range(8))
        assert {card["status"] for card in offline.data["cards"]} == {"unreachable"}


async def test_job_logs_are_limited_and_carry_the_fault(session_factory) -> None:
    await _seeded(session_factory)
    async with session_factory() as db:
        logs = await get_job_logs(db, "gpu-029")
        assert logs.ok and logs.data is not None
        assert logs.data["truncated"] is False
        joined = " ".join(logs.data["lines"])
        assert "heartbeat missed" in joined
        assert "ib0 link down" in joined

        clipped = await get_job_logs(db, "gpu-029", limit=3)
        assert clipped.data is not None
        assert clipped.data["truncated"] is True
        assert len(clipped.data["lines"]) == 3
        assert clipped.data["total_lines"] > 3


async def test_unknown_node_is_a_structured_not_found(session_factory) -> None:
    await _seeded(session_factory)
    async with session_factory() as db:
        response = await get_node_gpus(db, "gpu-999")
        assert response.ok is False
        assert response.data is None
        assert response.error is not None
        assert response.error.code == "not_found"
        assert "gpu-999" in response.error.message
        assert response.as_dict()["error"] == {
            "code": "not_found",
            "message": response.error.message,
        }


async def test_invalid_arguments_are_rejected_before_any_query(session_factory) -> None:
    async with session_factory() as db:
        bad_host = await get_node_status(db, "GPU-3")
        assert bad_host.ok is False
        assert bad_host.error is not None and bad_host.error.code == "invalid_args"
        assert bad_host.error.message.startswith("hostname:")

        bad_limit = await get_job_logs(db, "gpu-003", limit=0)
        assert bad_limit.error is not None and bad_limit.error.code == "invalid_args"
        oversized = await get_job_logs(db, "gpu-003", limit=500)
        assert oversized.error is not None and oversized.error.code == "invalid_args"


async def test_slow_tool_is_cut_off_at_the_timeout(session_factory, monkeypatch) -> None:
    async def slow_load(db, hostname):
        await asyncio.sleep(0.5)
        raise AssertionError("the timeout should fire before the query completes")

    monkeypatch.setattr(node_tools, "load_node", slow_load)
    async with session_factory() as db:
        response = await get_node_status(db, "gpu-003", timeout=0.01)
    assert response.ok is False
    assert response.error is not None and response.error.code == "timeout"
    assert "exceeded" in response.error.message
