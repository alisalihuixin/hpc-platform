"""Agent tools (F2).

Each tool validates its arguments with Pydantic, enforces the 10s timeout and
returns a structured error instead of raising - a stack trace in the model's
context is a hallucination magnet.
"""

from __future__ import annotations

__all__: list[str] = []
