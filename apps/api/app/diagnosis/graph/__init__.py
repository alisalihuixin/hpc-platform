"""LangGraph orchestration (F3/F6).

``DiagnosticState`` plus the plan/observe/reflect/report nodes.  Hard guardrails
live here, not in prompts: max 6 iterations, tool+arg de-duplication and a
decreasing token budget.
"""

from __future__ import annotations

__all__: list[str] = []
