"""Streaming diagnosis run (D54): persist every graph step, push it as SSE.

The single-run endpoint owns the investigation: ``GET /diagnosis/{id}/stream``
opens a ``pending`` run, drives the graph with ``astream`` and yields one SSE
frame per node *after* writing that node to ``diagnosis_steps``.  Persisting
before yielding means the timeline a viewer sees and the audit trail an operator
replays can never disagree, and a dropped connection still leaves a closed run
instead of one stuck in ``running``.

Runs that are already finished are replayed from the database, so refreshing the
report page does not spend another LLM request.  A run owned by another live
stream is only replayed - two viewers must never execute the same investigation.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.diagnosis.budget import TokenBudget
from app.diagnosis.graph.graph import (
    GRAPH_RECURSION_LIMIT,
    AlertContext,
    Completer,
    DiagnosticState,
    build_diagnosis_graph,
    build_initial_state,
)
from app.diagnosis.tools.base import elapsed_ms
from app.models.diagnosis import (
    Alert,
    AlertStatus,
    DiagnosisRun,
    DiagnosisStep,
    IncidentNode,
    RunStatus,
    StepPhase,
)
from app.services import diagnosis_service

#: Statuses that can still produce new steps; anything else is a replay.
LIVE_STATUSES = frozenset({RunStatus.PENDING.value, RunStatus.RUNNING.value})

#: Graph node names do not all match the persisted ``StepPhase`` vocabulary.
_NODE_TO_STEP: dict[str, str] = {
    "plan": StepPhase.PLAN.value,
    "call_tool": StepPhase.TOOL.value,
    "observe": StepPhase.OBSERVE.value,
    "reflect": StepPhase.REFLECT.value,
    "report": StepPhase.REPORT.value,
    "fallback": StepPhase.FALLBACK.value,
}

_CONNECTED = ": connected\n\n"


async def stream_diagnosis(
    db: AsyncSession,
    run: DiagnosisRun,
    *,
    llm: Completer | None = None,
    budget: TokenBudget | None = None,
) -> AsyncGenerator[str, None]:
    """Yield SSE frames for one run, executing it when it is still pending."""
    yield _CONNECTED
    steps = await diagnosis_service.list_steps(db, run.id)
    for step in steps:
        yield _step_frame(run.id, step)

    if run.status not in LIVE_STATUSES:
        yield _finished_frame(run)
        return
    if run.status != RunStatus.PENDING.value:
        # Another viewer's stream is driving this run: replay what exists and
        # leave the execution (and its terminal event) to that stream.
        yield _status_frame(run)
        return

    started = time.perf_counter()
    run_id = run.id
    alert_id = run.alert_id
    run.status = RunStatus.RUNNING.value
    await db.commit()

    alert = await db.get(Alert, run.alert_id)
    state = build_initial_state(await _alert_context(db, alert))
    graph = build_diagnosis_graph(db=db, llm=llm, budget=budget)
    seq = steps[-1].seq if steps else 0

    try:
        yield _status_frame(run)
        async for update in graph.astream(
            state, stream_mode="updates", config={"recursion_limit": GRAPH_RECURSION_LIMIT}
        ):
            for node_name, changes in update.items():
                state.update(changes)
                seq += 1
                step = _build_step(run_id, seq, node_name, changes, state)
                db.add(step)
                await db.commit()
                await db.refresh(step)
                yield _step_frame(run_id, step)
    except (asyncio.CancelledError, GeneratorExit):
        # The viewer went away mid-investigation.  Close the run so the alert
        # does not stay `diagnosing` - which would block every retry with 409.
        await db.rollback()
        await _finalise(
            db,
            run_id,
            alert_id,
            state,
            started,
            status=RunStatus.FAILED.value,
            error="stream: client disconnected",
        )
        raise
    except Exception as exc:  # noqa: BLE001 - a graph bug must still close the run
        await db.rollback()
        final_run = await _finalise(
            db,
            run_id,
            alert_id,
            state,
            started,
            status=RunStatus.FAILED.value,
            error=f"stream: {exc}",
        )
        yield _finished_frame(final_run)
        return

    final_run = await _finalise(db, run_id, alert_id, state, started)
    yield _finished_frame(final_run)


async def _alert_context(db: AsyncSession, alert: Alert | None) -> AlertContext:
    """Briefing handed to the graph; ``hostname`` comes from the mock fleet."""
    if alert is None:  # pragma: no cover - the run's FK cascades on delete
        return {}
    hostname = None
    if alert.node_id is not None:
        node = await db.get(IncidentNode, alert.node_id)
        hostname = node.hostname if node is not None else None
    return {
        "alert_id": alert.id,
        "script_key": alert.script_key,
        "severity": alert.severity,
        "summary": alert.summary,
        "hostname": hostname,
    }


def _build_step(
    run_id: int,
    seq: int,
    node_name: str,
    changes: dict[str, Any],
    state: DiagnosticState,
) -> DiagnosisStep:
    """Project one graph update onto the step row the timeline renders."""
    tool_name: str | None = None
    args: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    latency_ms = 0

    if node_name == "plan":
        decision = changes.get("plan") or {}
        tool_name = decision.get("tool") or None
        args = decision.get("args") or None
        result = {"done": bool(decision.get("done")), "reason": decision.get("reason", "")}
    elif node_name == "call_tool":
        response = changes.get("pending_result") or {}
        decision = state.get("plan", {})
        tool_name = response.get("tool") or decision.get("tool") or None
        args = decision.get("args") or None
        result = response
        latency_ms = int(response.get("latency_ms") or 0)
    elif node_name == "observe":
        evidence = changes.get("evidence") or state.get("evidence") or []
        entry = evidence[-1] if evidence else {}
        tool_name = entry.get("tool")
        args = entry.get("args")
        result = entry
    elif node_name == "reflect":
        result = changes.get("reflection") or {}
    else:  # report / fallback
        result = changes.get("report") or {}

    return DiagnosisStep(
        run_id=run_id,
        seq=seq,
        node=_NODE_TO_STEP[node_name],
        tool_name=tool_name,
        args=args,
        result=result or None,
        latency_ms=latency_ms,
    )


async def _finalise(
    db: AsyncSession,
    run_id: int,
    alert_id: int,
    state: DiagnosticState,
    started: float,
    *,
    status: str | None = None,
    error: str | None = None,
) -> DiagnosisRun:
    """Copy the graph's verdict onto the run and release the alert."""
    # Re-read after a rollback: the instance handed to the generator has had its
    # attributes expired, so touching them outside a greenlet would explode.
    run = await db.get(DiagnosisRun, run_id)
    assert run is not None  # the stream is only opened for an existing run
    run.status = status or state.get("status") or RunStatus.FAILED.value
    report = state.get("report") or {}
    run.root_cause = report.get("root_cause")
    run.confidence = report.get("confidence")
    run.iterations = int(state.get("iterations", 0))
    run.tokens = int((state.get("budget") or {}).get("used", 0))
    run.error = error or state.get("error")
    run.duration_ms = elapsed_ms(started)

    alert = await db.get(Alert, alert_id)
    if alert is not None:
        # A finished investigation closes the alert; a failed one reopens it so
        # an operator can retry without a manual DB edit.
        alert.status = (
            AlertStatus.RESOLVED.value
            if run.status in {RunStatus.SUCCEEDED.value, RunStatus.DEGRADED.value}
            else AlertStatus.OPEN.value
        )
    await db.commit()
    await db.refresh(run)
    return run


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _at() -> str:
    return datetime.now(UTC).isoformat()


def _step_frame(run_id: int, step: DiagnosisStep) -> str:
    return _sse(
        "diagnosis.step",
        {
            "type": "diagnosis.step",
            "run_id": run_id,
            "step": {
                "id": step.id,
                "seq": step.seq,
                "node": step.node,
                "tool_name": step.tool_name,
                "args": step.args,
                "result": step.result,
                "latency_ms": step.latency_ms,
                "created_at": step.created_at.isoformat(),
            },
            "at": _at(),
        },
    )


def _status_frame(run: DiagnosisRun) -> str:
    return _sse(
        "run.status",
        {"type": "run.status", "run_id": run.id, "status": run.status, "at": _at()},
    )


def _finished_frame(run: DiagnosisRun) -> str:
    return _sse(
        "run.finished",
        {
            "type": "run.finished",
            "run_id": run.id,
            "status": run.status,
            "root_cause": run.root_cause,
            "confidence": run.confidence,
            "iterations": run.iterations,
            "tokens": run.tokens,
            "duration_ms": run.duration_ms,
            "error": run.error,
            "at": _at(),
        },
    )


__all__ = ["LIVE_STATUSES", "stream_diagnosis"]
