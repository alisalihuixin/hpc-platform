"""Agent tools (F2).

Each tool validates its arguments with Pydantic, enforces the 10s timeout and
returns a structured error instead of raising - a stack trace in the model's
context is a hallucination magnet.

``TOOLS`` is the model-selectable registry.  ``execute_fix`` is exported for the
approval API but intentionally absent from it: the agent may propose a fix, a
human applies it.
"""

from __future__ import annotations

from app.diagnosis.tools.base import TOOL_TIMEOUT_SECONDS, ToolErrorCode, ToolResponse
from app.diagnosis.tools.fix import execute_fix
from app.diagnosis.tools.logs import get_job_logs
from app.diagnosis.tools.metrics import get_metrics
from app.diagnosis.tools.node import get_node_gpus, get_node_status
from app.diagnosis.tools.runbooks import search_runbooks

TOOLS = {
    "get_node_status": get_node_status,
    "get_job_logs": get_job_logs,
    "get_node_gpus": get_node_gpus,
    "get_metrics": get_metrics,
    "search_runbooks": search_runbooks,
}

__all__ = [
    "TOOLS",
    "TOOL_TIMEOUT_SECONDS",
    "ToolErrorCode",
    "ToolResponse",
    "execute_fix",
    "get_job_logs",
    "get_metrics",
    "get_node_gpus",
    "get_node_status",
    "search_runbooks",
]
