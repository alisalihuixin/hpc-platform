"""Single-case diagnosis graph (D51-D53).

The investigation loop is:

``plan`` -> ``call_tool`` -> ``observe`` -> ``plan``

``plan`` asks the model for exactly one next action (a tool call or a "done"
signal) in JSON mode.  ``call_tool`` runs that tool through the read-only
registry.  ``observe`` turns the structured result into an evidence entry with a
stable ``evidence_id`` and feeds a compact transcript back to the model.

Once the model says "done", ``reflect`` self-checks the chain: if evidence is
still missing it routes back to ``plan``, otherwise ``report`` writes the only
artefact that may carry a root cause.  The report validator rejects conclusions
that cite no evidence or an id that does not exist, so a hallucinated citation
fails the run instead of reaching an operator.

D53 adds the four hard guardrails that stop a confused agent from looping or
burning money: at most ``MAX_ITERATIONS`` tool calls, a LangGraph super-step
ceiling, tool+argument de-duplication, and the token ledger.  Any of them
diverts the run to ``fallback``, which writes a rule-generated report citing the
evidence collected so far; a run with no evidence at all stays ``degraded``
without a report.

Everything the nodes need - DB session, tool table, LLM client, token budget -
is injected through :func:`build_diagnosis_graph`, so a test can compile the
graph with a fake model and no network.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Literal, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm import ChatModel, LLMError, LLMResponse, Message, get_llm_client
from app.diagnosis.budget import TokenBudget
from app.diagnosis.graph.report import (
    Report,
    ReportError,
    build_fallback_report,
    validate_report,
)
from app.diagnosis.tools import TOOLS, ToolErrorCode, ToolResponse
from app.models.diagnosis import RunStatus


#: Shape of one evidence entry; the report (D52) cites ``evidence_id`` only.
class Evidence(TypedDict):
    evidence_id: str
    tool: str
    args: dict[str, Any]
    ok: bool
    latency_ms: int
    data: dict[str, Any] | None
    error: dict[str, str] | None


class PlanDecision(TypedDict):
    #: Present when the model wants a tool call; absent on the "done" branch.
    tool: str
    args: dict[str, Any]
    reason: str
    done: bool


class AlertContext(TypedDict, total=False):
    alert_id: int
    script_key: str
    severity: str
    summary: str
    hostname: str | None


class Reflection(TypedDict):
    #: ``False`` sends the graph back to ``plan`` for more evidence.
    sufficient: bool
    reason: str


class DiagnosticState(TypedDict, total=False):
    alert: AlertContext
    messages: list[Message]
    plan: PlanDecision
    pending_tool: str
    pending_result: dict[str, Any]
    evidence: list[Evidence]
    iterations: int
    #: How many times ``reflect`` ran; capped so "done + insufficient" cannot
    #: ping-pong forever without executing a tool.
    reflections: int
    budget: dict[str, int | bool]
    reflection: Reflection
    #: Serialised :class:`app.diagnosis.graph.report.Report`.
    report: dict[str, Any]
    status: str
    error: str | None


class Completer(Protocol):
    """The slice of :class:`app.core.llm.DeepSeekClient` the graph uses."""

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: ChatModel = ChatModel.CHAT,
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> LLMResponse: ...


class PlanError(RuntimeError):
    """The model answered with a payload that is not a usable plan."""


class ReflectError(RuntimeError):
    """The self-check reply is not a usable ``{sufficient, reason}`` object."""


#: Tool catalogue shown to the model.  Kept next to the graph, not inside the
#: prompt string, so D59 can tune selection without touching node logic.
TOOL_CATALOGUE: dict[str, str] = {
    "get_node_status": '{"hostname": "gpu-003"} - 节点状态/schedulable/心跳',
    "get_node_gpus": '{"hostname": "gpu-003"} - 每张卡的 ECC/Xid/温度/利用率',
    "get_job_logs": '{"hostname": "gpu-003", "limit": 50} - 最近作业日志',
    "get_metrics": '{"window": 60} - 集群聚合指标，window<=180 分钟',
    "search_runbooks": '{"query": "Xid", "tags": "nccl,network"} - 检索处置手册',
}

SYSTEM_PROMPT = """你是 HPC 集群的首席运维工程师，负责根据告警定位根因。
你只能调用下列只读工具，修复动作只能建议、不能执行：
{catalogue}

每次只输出一个 JSON 对象，不要输出其他文本：
- 需要证据：{{"tool": "<工具名>", "args": {{...}}, "reason": "<为什么查它>"}}
- 证据已足够：{{"done": true, "reason": "<结论依据>"}}
不要重复调用已经返回过结果的工具；先看已有证据，再决定下一步。"""

REFLECT_PROMPT = """请自检当前证据链是否足以定位根因。
只输出一个 JSON 对象：
{{"sufficient": true 或 false, "reason": "<缺哪类证据，或为什么已经足够>"}}
证据不足时指出缺口（例如还缺 GPU 级 ECC、作业日志、同交换机节点状态），不要臆测结论。"""

REPORT_PROMPT = """证据收集完成，请写出最终诊断报告。
只能引用下列 evidence_id：{evidence_ids}
每条结论都必须有证据支撑；无法映射到上述 id 的推测一律不要写。
只输出一个 JSON 对象：
{{"root_cause": "<根因，<=255 字>", "confidence": <0 到 1 的小数>,
  "evidence_refs": ["E01"], "fix_proposal": "<建议的修复/处置动作>",
  "reasoning": "<证据如何推出结论>"}}
evidence_refs 不能为空，且只能使用上面列出的 id。"""

#: Hard cap on executed tool calls per run (D53).  Six is the product decision:
#: enough for the deepest trap case, small enough to bound cost and latency.
MAX_ITERATIONS = 6

#: How many self-checks may ask for more evidence before the run degrades.
MAX_REFLECTIONS = 2

#: LangGraph super-step ceiling.  One tool round costs three super-steps
#: (plan, call_tool, observe), so the cap above needs 3x plus room for the
#: reflect/report/fallback hops.
GRAPH_RECURSION_LIMIT = MAX_ITERATIONS * 3 + 8


def build_diagnosis_graph(
    *,
    db: AsyncSession,
    llm: Completer | None = None,
    tools: dict[str, Any] | None = None,
    budget: TokenBudget | None = None,
) -> Any:
    """Compile the graph with every side effect injected (tests pass fakes)."""
    tool_table = tools if tools is not None else TOOLS
    completer = llm if llm is not None else get_llm_client()
    ledger = budget if budget is not None else TokenBudget()

    async def plan(state: DiagnosticState) -> dict[str, Any]:
        # Guardrail 1: the iteration cap is checked before spending a request.
        if state.get("iterations", 0) >= MAX_ITERATIONS:
            return {
                "status": RunStatus.DEGRADED.value,
                "plan": _done("iteration cap reached"),
                "budget": ledger.as_dict(),
            }
        # Guardrail 2: a degraded run must not spend another request.
        if ledger.degraded:
            return {
                "status": RunStatus.DEGRADED.value,
                "plan": _done("token budget exhausted before the next step"),
                "budget": ledger.as_dict(),
            }

        try:
            response = await completer.complete(state["messages"], json_mode=True)
        except LLMError as exc:
            return {
                "status": RunStatus.FAILED.value,
                "error": f"plan: {exc}",
                "plan": _done("llm unavailable"),
                "budget": ledger.as_dict(),
            }

        ledger.charge(response.total_tokens)
        try:
            decision = _parse_plan(response.parse_json(), tool_table)
        except (LLMError, PlanError) as exc:
            return {
                "status": RunStatus.FAILED.value,
                "error": f"plan: {exc}",
                "plan": _done("invalid plan"),
                "budget": ledger.as_dict(),
            }

        # Guardrail 3: the same tool with the same arguments is never executed
        # twice.  Repeating it means the model is stuck, so degrade now instead
        # of paying for a loop.
        if not decision["done"] and _signature(decision["tool"], decision["args"]) in {
            _signature(entry["tool"], entry["args"]) for entry in state.get("evidence", [])
        }:
            return {
                "status": RunStatus.DEGRADED.value,
                "plan": _done("duplicate tool call"),
                "messages": [
                    *state["messages"],
                    {"role": "assistant", "content": response.content},
                ],
                "budget": ledger.as_dict(),
            }

        update: dict[str, Any] = {
            "plan": decision,
            "pending_tool": "" if decision["done"] else decision["tool"],
            "messages": [
                *state["messages"],
                {"role": "assistant", "content": response.content},
            ],
            # A satisfied plan is not a success yet: ``reflect`` may ask for
            # more evidence and only ``report`` may close the run.
            "status": RunStatus.RUNNING.value,
            "budget": ledger.as_dict(),
        }
        return update

    async def call_tool(state: DiagnosticState) -> dict[str, Any]:
        decision = state["plan"]
        result = await invoke_tool(db, decision["tool"], decision.get("args") or {}, tool_table)
        return {
            "pending_result": result.as_dict(),
            "iterations": state.get("iterations", 0) + 1,
        }

    async def observe(state: DiagnosticState) -> dict[str, Any]:
        result = state["pending_result"]
        evidence_id = f"E{len(state.get('evidence', [])) + 1:02d}"
        entry: Evidence = {
            "evidence_id": evidence_id,
            "tool": result["tool"],
            "args": state["plan"].get("args") or {},
            "ok": result["ok"],
            "latency_ms": result["latency_ms"],
            "data": result["data"],
            "error": result["error"],
        }
        # The model sees a compact transcript, not the raw row - keeps the
        # context small and makes every claim traceable to an evidence id.
        transcript = {
            "evidence_id": evidence_id,
            "tool": entry["tool"],
            "args": entry["args"],
            "ok": entry["ok"],
            "data": entry["data"] or entry["error"],
        }
        return {
            "evidence": [*state.get("evidence", []), entry],
            "messages": [
                *state["messages"],
                {
                    "role": "user",
                    "content": json.dumps(transcript, ensure_ascii=False, default=str),
                },
            ],
            "pending_tool": "",
            "pending_result": {},
        }

    async def reflect(state: DiagnosticState) -> dict[str, Any]:
        rounds = state.get("reflections", 0)
        # Guardrail 4: "done" + "insufficient" can ping-pong without ever
        # calling a tool, so reflections get their own small cap.
        if rounds >= MAX_REFLECTIONS:
            return {
                "reflection": {"sufficient": False, "reason": "reflection cap reached"},
                "status": RunStatus.DEGRADED.value,
                "budget": ledger.as_dict(),
            }
        # No evidence means nothing to conclude, and asking the model would
        # only burn tokens: nudge it to collect first; ``plan`` owns the cap.
        if not state.get("evidence"):
            return {
                "reflection": {"sufficient": False, "reason": "尚无任何工具证据"},
                "messages": [
                    *state["messages"],
                    {
                        "role": "user",
                        "content": "当前没有任何工具证据，请先调用工具，不要直接下结论。",
                    },
                ],
                "reflections": rounds + 1,
                "status": RunStatus.RUNNING.value,
            }
        if ledger.degraded:
            return {
                "reflection": {"sufficient": True, "reason": "token budget exhausted"},
                "status": RunStatus.DEGRADED.value,
                "budget": ledger.as_dict(),
            }

        try:
            response = await completer.complete(
                [*state["messages"], {"role": "user", "content": REFLECT_PROMPT}],
                json_mode=True,
            )
        except LLMError as exc:
            return {
                "status": RunStatus.FAILED.value,
                "error": f"reflect: {exc}",
                "budget": ledger.as_dict(),
            }

        ledger.charge(response.total_tokens)
        try:
            reflection = _parse_reflection(response.parse_json())
        except (LLMError, ReflectError) as exc:
            return {
                "status": RunStatus.FAILED.value,
                "error": f"reflect: {exc}",
                "budget": ledger.as_dict(),
            }

        return {
            "reflection": reflection,
            "messages": [
                *state["messages"],
                {"role": "assistant", "content": response.content},
            ],
            "reflections": rounds + 1,
            "budget": ledger.as_dict(),
        }

    async def report(state: DiagnosticState) -> dict[str, Any]:
        evidence_ids = [entry["evidence_id"] for entry in state.get("evidence", [])]
        # Degraded runs stop before the LLM; ``fallback`` writes the rule report.
        if ledger.degraded:
            return {
                "status": RunStatus.DEGRADED.value,
                "budget": ledger.as_dict(),
            }

        prompt = REPORT_PROMPT.format(evidence_ids=", ".join(evidence_ids) or "（无）")
        try:
            response = await completer.complete(
                [*state["messages"], {"role": "user", "content": prompt}],
                json_mode=True,
            )
        except LLMError as exc:
            return {
                "status": RunStatus.FAILED.value,
                "error": f"report: {exc}",
                "budget": ledger.as_dict(),
            }

        ledger.charge(response.total_tokens)
        try:
            parsed = validate_report(response.parse_json(), evidence_ids)
        except (LLMError, ReportError) as exc:
            return {
                "status": RunStatus.FAILED.value,
                "error": f"report: {exc}",
                "budget": ledger.as_dict(),
            }

        return {
            "report": parsed.model_dump(),
            "status": RunStatus.SUCCEEDED.value,
            "messages": [
                *state["messages"],
                {"role": "assistant", "content": response.content},
            ],
            "budget": ledger.as_dict(),
        }

    async def fallback(state: DiagnosticState) -> dict[str, Any]:
        # Rule-only exit: no LLM call, so it works offline and cannot overspend.
        try:
            report = build_fallback_report(state.get("evidence", []))
        except ReportError as exc:
            return {
                "status": RunStatus.DEGRADED.value,
                "error": f"fallback: {exc}",
                "budget": ledger.as_dict(),
            }
        return {
            "report": report.model_dump(),
            "status": RunStatus.DEGRADED.value,
            "budget": ledger.as_dict(),
        }

    graph = StateGraph(DiagnosticState)
    graph.add_node("plan", plan)
    graph.add_node("call_tool", call_tool)
    graph.add_node("observe", observe)
    graph.add_node("reflect", reflect)
    graph.add_node("report", report)
    graph.add_node("fallback", fallback)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges(
        "plan",
        route_after_plan,
        {"call_tool": "call_tool", "reflect": "reflect", "fallback": "fallback", "end": END},
    )
    graph.add_edge("call_tool", "observe")
    graph.add_edge("observe", "plan")
    graph.add_conditional_edges(
        "reflect",
        route_after_reflect,
        {"plan": "plan", "report": "report", "fallback": "fallback", "end": END},
    )
    graph.add_conditional_edges(
        "report",
        route_after_report,
        {"fallback": "fallback", "end": END},
    )
    graph.add_edge("fallback", END)
    return graph.compile()


def route_after_plan(
    state: DiagnosticState,
) -> Literal["call_tool", "reflect", "fallback", "end"]:
    """A live tool request loops; "done" goes to self-check; failures stop."""
    status = state.get("status")
    if status == RunStatus.FAILED.value:
        return "end"
    if status == RunStatus.DEGRADED.value:
        return "fallback"
    if state.get("pending_tool"):
        return "call_tool"
    return "reflect" if state.get("plan", {}).get("done") else "end"


def route_after_reflect(state: DiagnosticState) -> Literal["plan", "report", "fallback", "end"]:
    """Missing evidence loops back; a satisfied self-check goes to the report."""
    status = state.get("status")
    if status == RunStatus.FAILED.value:
        return "end"
    if status == RunStatus.DEGRADED.value:
        return "fallback"
    return "report" if state.get("reflection", {}).get("sufficient") else "plan"


def route_after_report(state: DiagnosticState) -> Literal["fallback", "end"]:
    """Only a degraded report needs the rule-based fallback."""
    return "fallback" if state.get("status") == RunStatus.DEGRADED.value else "end"


async def invoke_tool(
    db: AsyncSession,
    tool: str,
    args: dict[str, Any],
    tools: dict[str, Any] | None = None,
) -> ToolResponse:
    """Adapt the registry's per-tool signatures to ``(name, args)``.

    A missing/extra argument from the model is a tool error, not a crash: the
    caller still gets a ``ToolResponse`` and the graph can recover.
    """
    table = tools if tools is not None else TOOLS
    handler = table.get(tool)
    if handler is None:
        raise PlanError(f"unknown tool {tool!r}")

    try:
        match tool:
            case "get_node_status" | "get_node_gpus":
                return await handler(db, args["hostname"])
            case "get_job_logs":
                return await handler(db, args["hostname"], limit=args.get("limit", 50))
            case "get_metrics":
                return await handler(db, window=args.get("window", 60))
            case "search_runbooks":
                return await handler(db, query=args.get("query"), tags=args.get("tags"))
            case _:
                raise PlanError(f"no argument mapping for tool {tool!r}")
    except KeyError as exc:
        return ToolResponse.failure(tool, ToolErrorCode.INVALID_ARGS, f"missing argument {exc}")


async def run_diagnosis(
    db: AsyncSession,
    alert: AlertContext,
    *,
    llm: Completer | None = None,
    budget: TokenBudget | None = None,
) -> DiagnosticState:
    """Convenience entry point: seed the state with the alert briefing."""
    graph = build_diagnosis_graph(db=db, llm=llm, budget=budget)
    initial: DiagnosticState = {
        "alert": alert,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT.format(catalogue=_catalogue())},
            {"role": "user", "content": _briefing(alert)},
        ],
        "evidence": [],
        "iterations": 0,
        "status": RunStatus.RUNNING.value,
    }
    # Belt and braces: even a graph bug cannot exceed the super-step ceiling.
    return await graph.ainvoke(initial, config={"recursion_limit": GRAPH_RECURSION_LIMIT})


def _catalogue() -> str:
    return "\n".join(f"- {name}: {usage}" for name, usage in TOOL_CATALOGUE.items())


def _briefing(alert: AlertContext) -> str:
    prompt = alert.get("summary", "")
    if alert.get("script_key"):
        prompt += f"\n（剧本：{alert['script_key']}）"
    if alert.get("hostname"):
        prompt += f"\n（涉及节点：{alert['hostname']}）"
    return prompt.strip()


def _done(reason: str) -> PlanDecision:
    return {"tool": "", "args": {}, "reason": reason, "done": True}


def _signature(tool: str, args: dict[str, Any]) -> str:
    """Stable identity for a tool+args pair, used by the duplicate guard."""
    return f"{tool}:{json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)}"


def _parse_plan(payload: dict[str, Any], tools: dict[str, Any]) -> PlanDecision:
    """Validate the JSON-mode reply before it can reach the tool table."""
    reason = str(payload.get("reason") or "")
    if payload.get("done") is True:
        return _done(reason)

    tool = payload.get("tool")
    if not isinstance(tool, str) or tool not in tools:
        raise PlanError(f"unknown tool {tool!r}")
    args = payload.get("args") or {}
    if not isinstance(args, dict):
        raise PlanError("plan.args must be an object")
    return {"tool": tool, "args": args, "reason": reason, "done": False}


def _parse_reflection(payload: dict[str, Any]) -> Reflection:
    """The self-check must answer the boolean explicitly; no guessing."""
    sufficient = payload.get("sufficient")
    if not isinstance(sufficient, bool):
        raise ReflectError("reflect.sufficient must be a boolean")
    return {
        "sufficient": sufficient,
        "reason": str(payload.get("reason") or "")[:500],
    }


__all__ = [
    "GRAPH_RECURSION_LIMIT",
    "MAX_ITERATIONS",
    "MAX_REFLECTIONS",
    "DiagnosticState",
    "Evidence",
    "PlanDecision",
    "PlanError",
    "REFLECT_PROMPT",
    "REPORT_PROMPT",
    "ReflectError",
    "Reflection",
    "Report",
    "ReportError",
    "SYSTEM_PROMPT",
    "TOOL_CATALOGUE",
    "build_diagnosis_graph",
    "build_fallback_report",
    "invoke_tool",
    "route_after_plan",
    "route_after_reflect",
    "route_after_report",
    "run_diagnosis",
    "validate_report",
]
