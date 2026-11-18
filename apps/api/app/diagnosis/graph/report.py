"""Structured diagnosis report with mandatory evidence citations (D52/D53).

A root cause is only allowed to exist as a :class:`Report`.  Every claim must
cite at least one ``evidence_id`` from the run's evidence chain; the prompt asks
the model to cite, but this validator is what *enforces* it.  A hallucinated
reference fails the run instead of reaching an operator, which is the whole
point of keeping the evidence chain in state.

Existence is the only requirement: failed tool calls are still real evidence
(they prove what the incident is *not*).  Degraded runs reuse the same schema
through :func:`build_fallback_report`, but with rule-generated text and zero
confidence, so a fallback can never masquerade as a model conclusion.
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


def build_fallback_report(evidence: Iterable[dict[str, Any]]) -> Report:
    """Rule-only conclusion written when a guardrail stops the agent (D53).

    This is deliberately *not* an LLM answer: it states that the run did not
    converge and cites the whole chain so an operator can replay it.  It still
    needs at least one evidence id - a run that never touched a tool has nothing
    to report and stays ``degraded`` with an error instead.
    """
    entries = list(evidence)
    if not entries:
        raise ReportError("cannot build a fallback report without evidence")

    tools = sorted({str(entry.get("tool")) for entry in entries})
    ok_count = sum(1 for entry in entries if entry.get("ok"))
    return Report(
        root_cause=f"证据不足，未能收敛到单一根因（{len(entries)} 条证据，其中 {ok_count} 条有效）",
        confidence=0.0,
        evidence_refs=[str(entry["evidence_id"]) for entry in entries],
        fix_proposal=f"人工复核证据链（涉及工具：{', '.join(tools)}）后重跑",
        reasoning="预算或步数护栏触发，本报告由规则生成，不代表模型结论",
    )
