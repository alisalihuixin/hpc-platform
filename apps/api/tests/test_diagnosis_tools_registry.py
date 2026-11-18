"""D45: metrics/runbook tools, the registry, and the un-registered fix proposal."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.diagnosis.data.mock import seed_diagnosis_data
from app.diagnosis.data.runbooks import RUNBOOKS, seed_runbooks
from app.diagnosis.data.scripts import seed_fault_scripts
from app.diagnosis.tools import TOOLS, execute_fix, get_metrics, search_runbooks
from app.models.diagnosis import Runbook


async def _seeded(session_factory) -> None:
    async with session_factory() as db:
        await seed_diagnosis_data(db)
        await seed_fault_scripts(db)
        await seed_runbooks(db)
        await db.commit()


def test_registry_exposes_five_tools_but_not_execute_fix() -> None:
    assert set(TOOLS) == {
        "get_node_status",
        "get_job_logs",
        "get_node_gpus",
        "get_metrics",
        "search_runbooks",
    }
    assert "execute_fix" not in TOOLS

    proposal = execute_fix("gpu-003", "replace_gpu", "ECC 不可纠正错误持续增长")
    assert proposal == {
        "status": "proposed",
        "requires_approval": True,
        "hostname": "gpu-003",
        "action": "replace_gpu",
        "reason": "ECC 不可纠正错误持续增长",
    }
    with pytest.raises(ValidationError):
        execute_fix("gpu-003", "reboot_everything", "all of it")  # type: ignore[arg-type]


async def test_metrics_aggregate_the_seeded_fleet(session_factory) -> None:
    await _seeded(session_factory)
    async with session_factory() as db:
        response = await get_metrics(db, window=180)
        assert response.ok and response.data is not None
        data = response.data
        assert data["window_minutes"] == 180
        assert data["nodes_total"] == 50
        assert data["nodes_online"] == 46
        assert data["nodes_degraded"] == 3
        assert data["nodes_offline"] == 1
        assert data["gpus_total"] == 360
        assert data["gpus_faulty"] == 12
        assert data["alerts_open"] == 20

        too_wide = await get_metrics(db, window=181)
        assert too_wide.ok is False
        assert too_wide.error is not None and too_wide.error.code == "invalid_args"


async def test_runbook_search_by_tag_and_text(session_factory) -> None:
    await _seeded(session_factory)
    async with session_factory() as db:
        by_tag = await search_runbooks(db, tags="nccl,network")
        assert by_tag.ok and by_tag.data is not None
        assert by_tag.data["count"] == 1
        assert by_tag.data["runbooks"][0]["title"] == "NCCL / IB 超时排查"
        assert by_tag.data["runbooks"][0]["excerpt"]

        by_query = await search_runbooks(db, query="Xid")
        assert by_query.data is not None
        assert [row["title"] for row in by_query.data["runbooks"]] == ["Xid 79 掉卡处置"]

        empty = await search_runbooks(db, tags="不存在的标签")
        assert empty.data is not None and empty.data["count"] == 0

        default_limit = await search_runbooks(db)
        assert default_limit.data is not None
        assert len(default_limit.data["runbooks"]) == 5

        too_many = await search_runbooks(db, limit=50)
        assert too_many.error is not None and too_many.error.code == "invalid_args"


async def test_runbook_seed_is_idempotent(session_factory) -> None:
    async with session_factory() as db:
        assert await seed_runbooks(db) == len(RUNBOOKS)
        await db.commit()

    async with session_factory() as db:
        assert await seed_runbooks(db) == 0
        await db.commit()
        assert await db.scalar(select(func.count()).select_from(Runbook)) == len(RUNBOOKS)
