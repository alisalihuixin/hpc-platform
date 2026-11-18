"""D51: the plan -> call_tool -> observe skeleton runs one round end to end.

A fake completion client keeps the test offline and deterministic: the first
"model" reply asks for one tool, the second says it is done.  What we assert is
the plumbing the later days build on - node order, evidence ids, iteration
accounting, budget charging and clean failures for impossible plans.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest

from app.core.llm import ChatModel, LLMResponse, LLMUnavailableError, Message
from app.diagnosis.budget import TokenBudget
from app.diagnosis.data.mock import seed_diagnosis_data
from app.diagnosis.data.runbooks import seed_runbooks
from app.diagnosis.data.scripts import seed_fault_scripts
from app.diagnosis.graph import (
    PlanError,
    ReportError,
    build_diagnosis_graph,
    invoke_tool,
    run_diagnosis,
    validate_report,
)

ALERT = {
    "alert_id": 1,
    "script_key": "gpu_ecc_uncorrectable",
    "severity": "critical",
    "summary": "gpu-003 GPU2 ECC 不可纠正错误 12 次，训练作业频繁中断",
    "hostname": "gpu-003",
}

TOOL_STEP = {
    "tool": "get_node_status",
    "args": {"hostname": "gpu-003"},
    "reason": "先确认节点是否在线",
}
GPU_STEP = {
    "tool": "get_node_gpus",
    "args": {"hostname": "gpu-003"},
    "reason": "再取每卡 ECC 计数",
}
DONE_STEP = {"done": True, "reason": "证据足够，GPU2 已上报 ECC 错误"}
REFLECT_OK = {"sufficient": True, "reason": "节点状态与卡片错误一致"}
REFLECT_NEED_MORE = {"sufficient": False, "reason": "还缺 GPU 级 ECC 计数"}
REPORT_STEP = {
    "root_cause": "gpu-003 GPU2 ECC 不可纠正错误",
    "confidence": 0.86,
    "evidence_refs": ["E01"],
    "fix_proposal": "将 GPU2 置为 drain 并安排 RMA 更换",
    "reasoning": "节点在线，告警指向 GPU2 硬件错误",
}


class FakeLLM:
    """Pops one canned reply per call and records the messages it received.

    A payload may be an exception instance: the fake raises it instead of
    answering, which lets one test simulate an outage mid-run.
    """

    def __init__(self, *payloads: dict[str, Any] | Exception) -> None:
        self._payloads = list(payloads)
        self.calls: list[list[Message]] = []
        self.json_modes: list[bool] = []

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: ChatModel = ChatModel.CHAT,
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        self.calls.append(list(messages))
        self.json_modes.append(json_mode)
        payload = self._payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return LLMResponse(
            content=json.dumps(payload, ensure_ascii=False),
            model="fake",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            latency_ms=1,
            attempts=1,
        )


class BrokenLLM:
    async def complete(self, messages: Sequence[Message], **_: Any) -> LLMResponse:
        return LLMResponse(
            content="not-json",
            model="fake",
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            latency_ms=1,
            attempts=1,
        )


class UnavailableLLM:
    async def complete(self, messages: Sequence[Message], **_: Any) -> LLMResponse:
        raise LLMUnavailableError("DeepSeek circuit is open; call skipped")


async def _seeded(session_factory) -> None:
    async with session_factory() as db:
        await seed_diagnosis_data(db)
        await seed_fault_scripts(db)
        await seed_runbooks(db)
        await db.commit()


async def test_dry_run_walks_every_node_once(session_factory) -> None:
    await _seeded(session_factory)
    llm = FakeLLM(TOOL_STEP, DONE_STEP, REFLECT_OK, REPORT_STEP)

    async with session_factory() as db:
        graph = build_diagnosis_graph(db=db, llm=llm)
        order: list[str] = []
        async for update in graph.astream(_initial_state(), stream_mode="updates"):
            order.extend(update)

    # Node order is the contract the SSE timeline (D54) renders.
    assert order == ["plan", "call_tool", "observe", "plan", "reflect", "report"]
    assert llm.json_modes == [True, True, True, True]


async def test_one_round_records_evidence_and_charges_budget(session_factory) -> None:
    await _seeded(session_factory)
    llm = FakeLLM(TOOL_STEP, DONE_STEP, REFLECT_OK, REPORT_STEP)

    async with session_factory() as db:
        state = await run_diagnosis(db, ALERT, llm=llm)

    assert state["status"] == "succeeded"
    assert state["iterations"] == 1
    assert state["budget"] == {"limit": 40_000, "used": 60, "remaining": 39_940, "degraded": False}

    (evidence,) = state["evidence"]
    assert evidence["evidence_id"] == "E01"
    assert evidence["tool"] == "get_node_status"
    assert evidence["args"] == {"hostname": "gpu-003"}
    assert evidence["ok"] is True
    assert evidence["data"]["hostname"] == "gpu-003"
    # The report keeps the citations the model wrote and closes the run.
    assert state["report"]["evidence_refs"] == ["E01"]
    assert state["report"]["root_cause"] == REPORT_STEP["root_cause"]
    assert state["report"]["confidence"] == pytest.approx(0.86)
    # The first prompt carries the tool catalogue plus the alert briefing.
    system, briefing = llm.calls[0][0]["content"], llm.calls[0][1]["content"]
    assert "get_node_gpus" in system and "只读工具" in system
    assert "gpu-003" in briefing and "ECC" in briefing
    # The observation is mirrored into the next prompt, tagged with its id.
    observed = json.loads(llm.calls[1][-1]["content"])
    assert observed["evidence_id"] == "E01" and observed["ok"] is True
    # The report prompt enumerates the ids the model is allowed to cite.
    report_prompt = llm.calls[-1][-1]["content"]
    assert "E01" in report_prompt and "只能引用下列 evidence_id" in report_prompt


async def test_tool_error_stays_in_the_evidence_chain(session_factory) -> None:
    await _seeded(session_factory)
    llm = FakeLLM(
        {"tool": "get_job_logs", "args": {"hostname": "gpu-999"}, "reason": "查日志"},
        DONE_STEP,
        REFLECT_OK,
        REPORT_STEP,
    )

    async with session_factory() as db:
        state = await run_diagnosis(db, ALERT, llm=llm)

    (evidence,) = state["evidence"]
    assert evidence["ok"] is False
    assert evidence["error"] == {
        "code": "not_found",
        "message": "node gpu-999 does not exist in the incident fleet",
    }
    # A failed lookup is information, not a run failure.
    assert state["status"] == "succeeded"


async def test_missing_tool_argument_becomes_structured_error(session_factory) -> None:
    await _seeded(session_factory)
    llm = FakeLLM(
        {"tool": "get_node_status", "args": {}, "reason": "漏了 hostname"},
        DONE_STEP,
        REFLECT_OK,
        REPORT_STEP,
    )

    async with session_factory() as db:
        state = await run_diagnosis(db, ALERT, llm=llm)

    (evidence,) = state["evidence"]
    assert evidence["ok"] is False
    assert evidence["error"]["code"] == "invalid_args"


async def test_reflect_sends_the_agent_back_for_missing_evidence(session_factory) -> None:
    await _seeded(session_factory)
    llm = FakeLLM(
        TOOL_STEP,
        DONE_STEP,
        REFLECT_NEED_MORE,
        GPU_STEP,
        DONE_STEP,
        REFLECT_OK,
        {**REPORT_STEP, "evidence_refs": ["E01", "E02"]},
    )

    async with session_factory() as db:
        graph = build_diagnosis_graph(db=db, llm=llm)
        order: list[str] = []
        state: dict[str, Any] = _initial_state()
        async for update in graph.astream(_initial_state(), stream_mode="updates"):
            for node, changes in update.items():
                order.append(node)
                state.update(changes)

    assert order == [
        "plan",
        "call_tool",
        "observe",
        "plan",
        "reflect",
        "plan",
        "call_tool",
        "observe",
        "plan",
        "reflect",
        "report",
    ]
    assert state["status"] == "succeeded"
    assert [entry["evidence_id"] for entry in state["evidence"]] == ["E01", "E02"]
    assert state["report"]["evidence_refs"] == ["E01", "E02"]
    # The second plan call saw the reflection feedback before choosing again.
    assert "还缺 GPU 级 ECC 计数" in llm.calls[3][-1]["content"]


async def test_report_without_evidence_refs_is_rejected(session_factory) -> None:
    await _seeded(session_factory)
    llm = FakeLLM(TOOL_STEP, DONE_STEP, REFLECT_OK, {**REPORT_STEP, "evidence_refs": []})

    async with session_factory() as db:
        state = await run_diagnosis(db, ALERT, llm=llm)

    assert state["status"] == "failed"
    assert state["error"].startswith("report:")
    assert "at least 1 item" in state["error"]
    assert "report" not in state


async def test_report_citing_unknown_evidence_is_rejected(session_factory) -> None:
    await _seeded(session_factory)
    llm = FakeLLM(TOOL_STEP, DONE_STEP, REFLECT_OK, {**REPORT_STEP, "evidence_refs": ["E99"]})

    async with session_factory() as db:
        state = await run_diagnosis(db, ALERT, llm=llm)

    assert state["status"] == "failed"
    assert "unknown evidence ids: E99" in state["error"]
    assert "report" not in state


async def test_reflect_rejects_a_non_boolean_verdict(session_factory) -> None:
    await _seeded(session_factory)
    llm = FakeLLM(TOOL_STEP, DONE_STEP, {"sufficient": "yes", "reason": "含糊"})

    async with session_factory() as db:
        state = await run_diagnosis(db, ALERT, llm=llm)

    assert state["status"] == "failed"
    assert state["error"] == "reflect: reflect.sufficient must be a boolean"


async def test_reflect_without_evidence_nudges_the_agent_to_collect(session_factory) -> None:
    await _seeded(session_factory)
    # "Done" before any lookup must not produce a report; reflect sends the
    # agent back and the second plan actually calls a tool.
    llm = FakeLLM(DONE_STEP, TOOL_STEP, DONE_STEP, REFLECT_OK, REPORT_STEP)

    async with session_factory() as db:
        graph = build_diagnosis_graph(db=db, llm=llm)
        order: list[str] = []
        async for update in graph.astream(_initial_state(), stream_mode="updates"):
            order.extend(update)

    assert order[:4] == ["plan", "reflect", "plan", "call_tool"]
    assert order[-1] == "report"
    assert "当前没有任何工具证据" in llm.calls[1][-1]["content"]


async def test_reflect_degrades_when_budget_is_crossed_mid_run(session_factory) -> None:
    await _seeded(session_factory)
    llm = FakeLLM(TOOL_STEP, DONE_STEP)

    async with session_factory() as db:
        state = await run_diagnosis(db, ALERT, llm=llm, budget=TokenBudget(limit=20))

    # Two 15-token plans cross the 20-token ceiling; reflect must not spend a
    # third request and hands the run to the D53 fallback report.
    assert len(llm.calls) == 2
    assert state["status"] == "degraded"
    assert "report" not in state
    assert state["budget"] == {"limit": 20, "used": 30, "remaining": 0, "degraded": True}


async def test_reflect_outage_fails_the_run(session_factory) -> None:
    await _seeded(session_factory)
    llm = FakeLLM(TOOL_STEP, DONE_STEP, LLMUnavailableError("upstream down"))

    async with session_factory() as db:
        state = await run_diagnosis(db, ALERT, llm=llm)

    assert state["status"] == "failed"
    assert state["error"] == "reflect: upstream down"
    assert len(state["evidence"]) == 1


async def test_report_outage_fails_the_run_after_a_sufficient_self_check(session_factory) -> None:
    await _seeded(session_factory)
    llm = FakeLLM(TOOL_STEP, DONE_STEP, REFLECT_OK, LLMUnavailableError("upstream down"))

    async with session_factory() as db:
        state = await run_diagnosis(db, ALERT, llm=llm)

    assert state["status"] == "failed"
    assert state["error"] == "report: upstream down"
    assert "report" not in state


async def test_report_stops_when_the_self_check_used_the_last_tokens(session_factory) -> None:
    await _seeded(session_factory)
    llm = FakeLLM(TOOL_STEP, DONE_STEP, REFLECT_OK)

    async with session_factory() as db:
        # 15 + 15 + 15 == 45: the reflect call is the one that latches degraded.
        state = await run_diagnosis(db, ALERT, llm=llm, budget=TokenBudget(limit=45))

    assert len(llm.calls) == 3
    assert state["status"] == "degraded"
    assert "report" not in state


def test_validate_report_rejects_empty_and_unknown_refs() -> None:
    payload = {**REPORT_STEP, "evidence_refs": []}
    with pytest.raises(ReportError, match="at least 1 item"):
        validate_report(payload, {"E01"})

    with pytest.raises(ReportError, match="unknown evidence ids: E02, E99"):
        validate_report({**REPORT_STEP, "evidence_refs": ["E01", "E02", "E99"]}, ["E01"])

    report = validate_report(REPORT_STEP, ["E01"])
    assert report.evidence_refs == ["E01"]
    assert report.confidence == pytest.approx(0.86)


@pytest.mark.parametrize(
    "payload",
    [
        {"tool": "execute_fix", "args": {"hostname": "gpu-003"}, "reason": "越权"},
        {"tool": "get_node_status", "args": "gpu-003", "reason": "args 不是对象"},
    ],
)
async def test_impossible_plans_fail_fast(session_factory, payload) -> None:
    await _seeded(session_factory)
    llm = FakeLLM(payload)

    async with session_factory() as db:
        state = await run_diagnosis(db, ALERT, llm=llm)

    assert state["status"] == "failed"
    assert state["error"].startswith("plan:")
    assert state["evidence"] == []
    assert len(llm.calls) == 1


async def test_non_json_reply_fails_the_run(session_factory) -> None:
    await _seeded(session_factory)

    async with session_factory() as db:
        state = await run_diagnosis(db, ALERT, llm=BrokenLLM())

    assert state["status"] == "failed"
    assert "non-JSON" in state["error"]


async def test_llm_outage_fails_the_run_without_evidence(session_factory) -> None:
    await _seeded(session_factory)

    async with session_factory() as db:
        state = await run_diagnosis(db, ALERT, llm=UnavailableLLM())

    assert state["status"] == "failed"
    assert "circuit is open" in state["error"]
    assert state["evidence"] == []


async def test_exhausted_budget_stops_before_the_next_request(session_factory) -> None:
    await _seeded(session_factory)
    llm = FakeLLM(TOOL_STEP, DONE_STEP)

    async with session_factory() as db:
        state = await run_diagnosis(db, ALERT, llm=llm, budget=TokenBudget(limit=10))

    # The first reply costs 15 tokens, so the loop may finish that round but
    # must not start another LLM request afterwards.
    assert len(llm.calls) == 1
    assert state["status"] == "degraded"
    assert state["budget"]["degraded"] is True
    assert len(state["evidence"]) == 1


async def test_invoke_tool_rejects_unknown_names_and_missing_args(session_factory) -> None:
    async with session_factory() as db:
        with pytest.raises(PlanError, match="unknown tool"):
            await invoke_tool(db, "execute_fix", {"hostname": "gpu-003"})

        response = await invoke_tool(db, "get_metrics", {"window": 60})
        assert response.ok is True

        runbooks = await invoke_tool(db, "search_runbooks", {"query": "Xid"})
        assert runbooks.ok is True

        # A registry entry without an argument mapping is a developer error.
        with pytest.raises(PlanError, match="no argument mapping"):
            await invoke_tool(db, "custom_tool", {}, tools={"custom_tool": lambda: None})

        missing = await invoke_tool(db, "get_node_gpus", {})
        assert missing.ok is False
        assert missing.error is not None and missing.error.code == "invalid_args"


def _initial_state() -> dict[str, Any]:
    return {
        "alert": ALERT,
        "messages": [
            {"role": "system", "content": "plan with tools"},
            {"role": "user", "content": ALERT["summary"]},
        ],
        "evidence": [],
        "iterations": 0,
        "status": "running",
    }
