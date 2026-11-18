"""D54: one SSE frame per persisted step, replay for finished runs.

The stream is the first place where the graph, the database and HTTP meet, so
the assertions cover all three: node order on the wire, ``diagnosis_steps`` rows
afterwards, and the run/alert status the report page (D55) will read.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.deps.diagnosis import get_completer
from app.diagnosis.data.mock import seed_diagnosis_data
from app.diagnosis.data.runbooks import seed_runbooks
from app.diagnosis.data.scripts import seed_fault_scripts
from app.main import app
from app.models.diagnosis import Alert, DiagnosisRun, DiagnosisStep, IncidentNode, RunStatus
from app.services.diagnosis_stream import stream_diagnosis
from tests.conftest import API, USER_EMAIL, USER_PASSWORD, auth_headers
from tests.test_diagnosis_graph import (
    DONE_STEP,
    REFLECT_OK,
    REPORT_STEP,
    TOOL_STEP,
    FakeLLM,
)

ALERT_SUMMARY = "gpu-003 GPU2 ECC 不可纠正错误 12 次，训练作业频繁中断"


class ExplodingLLM:
    """Any model call is a bug: terminal runs must be replayed, not re-run."""

    async def complete(self, *_: Any, **__: Any) -> Any:  # pragma: no cover - asserts
        raise AssertionError("the model must not be called for a non-pending run")


class CrashingLLM:
    async def complete(self, *_: Any, **__: Any) -> Any:
        raise RuntimeError("boom")


async def _seeded(session_factory) -> None:
    async with session_factory() as db:
        await seed_diagnosis_data(db)
        await seed_fault_scripts(db)
        await seed_runbooks(db)
        await db.commit()


async def _make_run(
    session_factory, *, status: str = RunStatus.PENDING.value, steps: int = 0
) -> tuple[int, int]:
    async with session_factory() as db:
        node = await db.scalar(select(IncidentNode).where(IncidentNode.hostname == "gpu-003"))
        alert = Alert(
            node_id=node.id,
            script_key="gpu_ecc_uncorrectable",
            severity="critical",
            summary=ALERT_SUMMARY,
            status="diagnosing",
        )
        db.add(alert)
        await db.flush()
        run = DiagnosisRun(alert_id=alert.id, status=status)
        db.add(run)
        await db.flush()
        for seq in range(1, steps + 1):
            db.add(
                DiagnosisStep(
                    run_id=run.id,
                    seq=seq,
                    node="plan",
                    result={"done": False, "reason": f"round {seq}"},
                )
            )
        await db.commit()
        return alert.id, run.id


def _events(frames: list[str]) -> list[dict[str, Any]]:
    return [
        json.loads(frame.split("data: ", 1)[1]) for frame in frames if frame.startswith("event: ")
    ]


async def test_stream_persists_every_node_in_execution_order(session_factory) -> None:
    await _seeded(session_factory)
    alert_id, run_id = await _make_run(session_factory)
    llm = FakeLLM(TOOL_STEP, DONE_STEP, REFLECT_OK, REPORT_STEP)

    async with session_factory() as db:
        run = await db.get(DiagnosisRun, run_id)
        frames = [frame async for frame in stream_diagnosis(db, run, llm=llm)]

    assert frames[0] == ": connected\n\n"
    events = _events(frames)
    assert [event["type"] for event in events] == [
        "run.status",
        *["diagnosis.step"] * 6,
        "run.finished",
    ]

    steps = [event["step"] for event in events if event["type"] == "diagnosis.step"]
    assert [step["node"] for step in steps] == [
        "plan",
        "tool",
        "observe",
        "plan",
        "reflect",
        "report",
    ]
    assert [step["seq"] for step in steps] == [1, 2, 3, 4, 5, 6]
    # The plan frame names the tool the model asked for; the tool frame carries
    # the measured latency and the raw result the timeline card renders.
    assert steps[0]["tool_name"] == "get_node_status"
    assert steps[0]["args"] == {"hostname": "gpu-003"}
    assert steps[1]["tool_name"] == "get_node_status"
    assert steps[1]["result"]["ok"] is True
    assert steps[1]["latency_ms"] == steps[1]["result"]["latency_ms"]
    assert steps[2]["result"]["evidence_id"] == "E01"
    assert steps[5]["result"]["root_cause"] == REPORT_STEP["root_cause"]

    finished = events[-1]
    assert finished["status"] == "succeeded"
    assert finished["root_cause"] == REPORT_STEP["root_cause"]
    assert finished["confidence"] == pytest.approx(0.86)
    assert finished["iterations"] == 1
    assert finished["tokens"] == 60
    assert finished["duration_ms"] >= 0
    assert finished["error"] is None

    async with session_factory() as db:
        stored_run = await db.get(DiagnosisRun, run_id)
        alert = await db.get(Alert, alert_id)
        stored_steps = list(
            await db.scalars(
                select(DiagnosisStep)
                .where(DiagnosisStep.run_id == run_id)
                .order_by(DiagnosisStep.seq)
            )
        )
    assert stored_run.status == "succeeded"
    assert stored_run.iterations == 1
    assert stored_run.tokens == 60
    assert stored_run.confidence == pytest.approx(0.86)
    assert alert.status == "resolved"
    assert [step.node for step in stored_steps] == [step["node"] for step in steps]


async def test_finished_run_is_replayed_without_calling_the_model(session_factory) -> None:
    await _seeded(session_factory)
    _, run_id = await _make_run(session_factory, status=RunStatus.SUCCEEDED.value, steps=2)

    async with session_factory() as db:
        run = await db.get(DiagnosisRun, run_id)
        frames = [frame async for frame in stream_diagnosis(db, run, llm=ExplodingLLM())]

    events = _events(frames)
    assert [event["type"] for event in events] == [
        "diagnosis.step",
        "diagnosis.step",
        "run.finished",
    ]
    assert [event["step"]["seq"] for event in events[:-1]] == [1, 2]
    assert events[-1]["status"] == "succeeded"


async def test_run_owned_by_another_viewer_is_not_executed_twice(session_factory) -> None:
    await _seeded(session_factory)
    _, run_id = await _make_run(session_factory, status=RunStatus.RUNNING.value, steps=1)

    async with session_factory() as db:
        run = await db.get(DiagnosisRun, run_id)
        frames = [frame async for frame in stream_diagnosis(db, run, llm=ExplodingLLM())]

    events = _events(frames)
    assert [event["type"] for event in events] == ["diagnosis.step", "run.status"]
    assert events[-1]["status"] == "running"


async def test_disconnect_closes_the_run_and_reopens_the_alert(session_factory) -> None:
    await _seeded(session_factory)
    alert_id, run_id = await _make_run(session_factory)
    llm = FakeLLM(TOOL_STEP, DONE_STEP, REFLECT_OK, REPORT_STEP)

    async with session_factory() as db:
        run = await db.get(DiagnosisRun, run_id)
        stream = stream_diagnosis(db, run, llm=llm)
        assert await anext(stream) == ": connected\n\n"
        assert json.loads((await anext(stream)).split("data: ", 1)[1])["status"] == "running"
        await stream.aclose()

    async with session_factory() as db:
        stored_run = await db.get(DiagnosisRun, run_id)
        alert = await db.get(Alert, alert_id)
    assert stored_run.status == "failed"
    assert stored_run.error == "stream: client disconnected"
    # Reopened so the operator can retry instead of being blocked by 409.
    assert alert.status == "open"


async def test_graph_crash_fails_the_run_without_leaving_it_running(session_factory) -> None:
    await _seeded(session_factory)
    alert_id, run_id = await _make_run(session_factory)

    async with session_factory() as db:
        run = await db.get(DiagnosisRun, run_id)
        frames = [frame async for frame in stream_diagnosis(db, run, llm=CrashingLLM())]

    events = _events(frames)
    assert events[0]["status"] == "running"
    assert events[-1]["type"] == "run.finished"
    assert events[-1]["status"] == "failed"
    assert events[-1]["error"] == "stream: boom"

    async with session_factory() as db:
        stored_run = await db.get(DiagnosisRun, run_id)
        alert = await db.get(Alert, alert_id)
    assert stored_run.status == "failed"
    assert alert.status == "open"


async def test_stream_endpoint_requires_authentication(client: AsyncClient, seeded) -> None:
    assert (await client.get(f"{API}/diagnosis/1/stream")).status_code == 401


async def test_stream_endpoint_is_404_for_unknown_run(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    response = await client.get(f"{API}/diagnosis/99999/stream", headers=headers)
    assert response.status_code == 404
    assert response.json()["detail"] == "Diagnosis run not found"


@pytest_asyncio.fixture
async def diagnosis_run(client: AsyncClient, session_factory, seeded) -> dict[str, int]:
    await _seeded(session_factory)
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    async with session_factory() as db:
        node = await db.scalar(select(IncidentNode).where(IncidentNode.hostname == "gpu-003"))
        node_id = node.id
    alert = await client.post(
        f"{API}/alerts",
        headers=headers,
        json={
            "node_id": node_id,
            "script_key": "gpu_ecc_uncorrectable",
            "severity": "critical",
            "summary": ALERT_SUMMARY,
        },
    )
    run = await client.post(
        f"{API}/diagnosis", headers=headers, json={"alert_id": alert.json()["id"]}
    )
    return {"alert_id": alert.json()["id"], "run_id": run.json()["id"]}


async def test_stream_endpoint_runs_the_agent_and_returns_sse(
    client: AsyncClient, session_factory, diagnosis_run: dict[str, int]
) -> None:
    app.dependency_overrides[get_completer] = lambda: FakeLLM(
        TOOL_STEP, DONE_STEP, REFLECT_OK, REPORT_STEP
    )
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)

    response = await client.get(
        f"{API}/diagnosis/{diagnosis_run['run_id']}/stream", headers=headers
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert body.startswith(": connected\n\n")
    assert body.count("event: diagnosis.step") == 6
    assert body.index("event: diagnosis.step") < body.index("event: run.finished")
    assert '"node": "report"' in body

    async with session_factory() as db:
        run = await db.get(DiagnosisRun, diagnosis_run["run_id"])
        steps = list(
            await db.scalars(
                select(DiagnosisStep)
                .where(DiagnosisStep.run_id == diagnosis_run["run_id"])
                .order_by(DiagnosisStep.seq)
            )
        )
    assert run.status == "succeeded"
    assert [step.seq for step in steps] == [1, 2, 3, 4, 5, 6]
