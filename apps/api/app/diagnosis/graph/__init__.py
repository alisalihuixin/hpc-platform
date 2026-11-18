"""LangGraph orchestration (F3/F6).

``DiagnosticState`` plus the plan/observe/reflect/report nodes.  Hard guardrails
live here, not in prompts: max 6 iterations, tool+arg de-duplication and a
decreasing token budget.
"""

from __future__ import annotations

from app.diagnosis.graph.graph import (
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
    invoke_tool,
    route_after_plan,
    route_after_reflect,
    run_diagnosis,
)
from app.diagnosis.graph.report import Report, ReportError, validate_report

__all__ = [
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
    "invoke_tool",
    "route_after_plan",
    "route_after_reflect",
    "run_diagnosis",
    "validate_report",
]
