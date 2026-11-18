"""LangGraph orchestration (F3/F6).

``DiagnosticState`` plus the plan/observe/reflect/report nodes.  Hard guardrails
live here, not in prompts: max 6 iterations, tool+arg de-duplication and a
decreasing token budget.
"""

from __future__ import annotations

from app.diagnosis.graph.graph import (
    GRAPH_RECURSION_LIMIT,
    MAX_ITERATIONS,
    MAX_REFLECTIONS,
    REFLECT_PROMPT,
    REPORT_PROMPT,
    SYSTEM_PROMPT,
    TOOL_CATALOGUE,
    DiagnosticState,
    Evidence,
    PlanDecision,
    PlanError,
    ReflectError,
    Reflection,
    build_diagnosis_graph,
    build_initial_state,
    invoke_tool,
    route_after_plan,
    route_after_reflect,
    route_after_report,
    run_diagnosis,
)
from app.diagnosis.graph.report import (
    Report,
    ReportError,
    build_fallback_report,
    validate_report,
)

__all__ = [
    "GRAPH_RECURSION_LIMIT",
    "MAX_ITERATIONS",
    "MAX_REFLECTIONS",
    "REFLECT_PROMPT",
    "REPORT_PROMPT",
    "SYSTEM_PROMPT",
    "TOOL_CATALOGUE",
    "DiagnosticState",
    "Evidence",
    "PlanDecision",
    "PlanError",
    "ReflectError",
    "Reflection",
    "Report",
    "ReportError",
    "build_diagnosis_graph",
    "build_fallback_report",
    "build_initial_state",
    "invoke_tool",
    "route_after_plan",
    "route_after_reflect",
    "route_after_report",
    "run_diagnosis",
    "validate_report",
]
