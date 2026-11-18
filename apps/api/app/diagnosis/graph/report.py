"""Structured diagnosis report with mandatory evidence citations (D52).

A root cause is only allowed to exist as a :class:`Report`.  Every claim must
cite at least one ``evidence_id`` from the run's evidence chain; the prompt asks
the model to cite, but this validator is what *enforces* it.  A hallucinated
reference fails the run instead of reaching an operator, which is the whole
point of keeping the evidence chain in state.

Existence is the only requirement: failed tool calls are still real evidence
(they prove what the incident is *not*), and D53's rule-based fallback reuses
the same schema.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ReportError(RuntimeError):
    """The model's report cannot be trusted as written."""


class Report(BaseModel):
    """Final answer persisted on the run and rendered by the report page (D55)."""

    model_config = ConfigDict(extra="ignore")

    root_cause: str = Field(min_length=2, max_length=255)
    #: Self-assessed; the eval harness (D57) compares it with the human verdict.
    confidence: float = Field(ge=0.0, le=1.0)
    #: At least one id, each of which must exist in the evidence chain.
    evidence_refs: list[str] = Field(min_length=1)
    fix_proposal: str = Field(min_length=2, max_length=1000)
    reasoning: str = Field(default="", max_length=2000)


def validate_report(payload: dict[str, Any], evidence_ids: Iterable[str]) -> Report:
    """Parse the model payload and reject citations outside the chain.

    Raises :class:`ReportError` for both schema violations (including an empty
    ``evidence_refs`` list) and unknown ids, so the caller has one failure path.
    """
    try:
        report = Report.model_validate(payload)
    except ValidationError as exc:
        raise ReportError(f"invalid report payload: {exc.errors()[0]['msg']}") from exc

    known = set(evidence_ids)
    unknown = sorted({ref for ref in report.evidence_refs if ref not in known})
    if unknown:
        raise ReportError(f"report cites unknown evidence ids: {', '.join(unknown)}")
    return report
