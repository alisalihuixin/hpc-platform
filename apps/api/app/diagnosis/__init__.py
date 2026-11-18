"""Cluster fault-diagnosis domain (project 2).

Layout mirrors the pipeline described in the PRD:

* ``data``  - deterministic mock fleet, the 20 fault scripts, eval ground truth
* ``tools`` - the six Pydantic-constrained tools the agent may call
* ``graph`` - LangGraph state machine (plan -> call_tool -> observe -> reflect)
* ``eval``  - offline evaluation of the 20-case benchmark
"""

from __future__ import annotations

__all__: list[str] = []
