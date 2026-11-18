"""Single-case diagnosis graph (D51).

Three nodes form the investigation loop:

``plan`` -> ``call_tool`` -> ``observe`` -> ``plan``

``plan`` asks the model for exactly one next action (a tool call or a "done"
signal) in JSON mode.  ``call_tool`` runs that tool through the read-only
registry.  ``observe`` turns the structured result into an evidence entry with a
stable ``evidence_id`` and feeds a compact transcript back to the model.

Everything the nodes need - DB session, tool table, LLM client, token budget -
is injected through :func:`build_diagnosis_graph`, so a test can compile the
graph with a fake model and no network.  The loop guardrails (iteration cap,
de-duplication) and the report node land in D52/D53; this module only proves the
skeleton compiles and walks one round end to end.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Literal, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm import ChatModel, LLMError, LLMResponse, Message, get_llm_client
from app.diagnosis.budget import TokenBudget
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


class DiagnosticState(TypedDict, total=False):
    alert: AlertContext
    messages: list[Message]
    plan: PlanDecision
    pending_tool: str
    pending_result: dict[str, Any]
    evidence: list[Evidence]
    iterations: int
    budget: dict[str, int | bool]
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
        # A degraded run must not spend another request; D53 writes the
        # rule-based conclusion, D51 only stops cleanly.
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

        update: dict[str, Any] = {
            "plan": decision,
            "pending_tool": "" if decision["done"] else decision["tool"],
            "messages": [
                *state["messages"],
                {"role": "assistant", "content": response.content},
            ],
            "status": (
                # D52 inserts the report node here; until then a satisfied plan
                # is a successful (report-less) run.
                RunStatus.SUCCEEDED.value if decision["done"] else RunStatus.RUNNING.value
            ),
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

    graph = StateGraph(DiagnosticState)
    graph.add_node("plan", plan)
    graph.add_node("call_tool", call_tool)
    graph.add_node("observe", observe)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges(
        "plan",
        route_after_plan,
        {"call_tool": "call_tool", "end": END},
    )
    graph.add_edge("call_tool", "observe")
    graph.add_edge("observe", "plan")
    return graph.compile()


def route_after_plan(state: DiagnosticState) -> Literal["call_tool", "end"]:
    """Only a live tool request loops; failures, degradation and "done" stop."""
    terminal = {RunStatus.FAILED.value, RunStatus.DEGRADED.value, RunStatus.SUCCEEDED.value}
    if state.get("status") in terminal:
        return "end"
    return "call_tool" if state.get("pending_tool") else "end"


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
    return await graph.ainvoke(initial)


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


__all__ = [
    "DiagnosticState",
    "Evidence",
    "PlanDecision",
    "PlanError",
    "SYSTEM_PROMPT",
    "TOOL_CATALOGUE",
    "build_diagnosis_graph",
    "invoke_tool",
    "route_after_plan",
    "run_diagnosis",
]
