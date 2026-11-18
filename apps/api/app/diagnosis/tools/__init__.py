"""Agent tools (F2).

Each tool validates its arguments with Pydantic, enforces the 10s timeout and
returns a structured error instead of raising - a stack trace in the model's
context is a hallucination magnet.
"""

from __future__ import annotations

from app.diagnosis.tools.base import TOOL_TIMEOUT_SECONDS, ToolResponse
from app.diagnosis.tools.logs import get_job_logs
from app.diagnosis.tools.node import get_node_gpus, get_node_status

__all__ = [
    "TOOL_TIMEOUT_SECONDS",
    "ToolResponse",
    "get_job_logs",
    "get_node_gpus",
    "get_node_status",
]
