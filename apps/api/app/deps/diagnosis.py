"""Diagnosis-specific dependencies.

The graph's model client is injected rather than imported inside the stream
service so a test can drive a whole run through HTTP with a fake model.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.llm import get_llm_client
from app.diagnosis.graph.graph import Completer


def get_completer() -> Completer:
    """Return the shared DeepSeek client (lazily constructed)."""
    return get_llm_client()


DiagnosisLLMDep = Annotated[Completer, Depends(get_completer)]

__all__ = ["DiagnosisLLMDep", "get_completer"]
