"""LangGraph orchestration (F3/F6).

``DiagnosticState`` plus the plan/observe/reflect/report nodes.  Hard guardrails
live here, not in prompts: max 6 iterations, tool+arg de-duplication and a
decreasing token budget.
"""

from __future__ import annotations

from app.diagnosis.graph.graph import (
    SYSTEM_PROMPT,
    TOOL_CATALOGUE,
    DiagnosticState,
    Evidence,
    PlanDecision,
    PlanError,
    build_diagnosis_graph,
    invoke_tool,
    route_after_plan,
    run_diagnosis,
)

__all__ = [
    "SYSTEM_PROMPT",
    "TOOL_CATALOGUE",
    "DiagnosticState",
    "Evidence",
    "PlanDecision",
    "PlanError",
    "build_diagnosis_graph",
    "invoke_tool",
    "route_after_plan",
    "run_diagnosis",
]
