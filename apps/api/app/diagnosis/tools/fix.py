"""Remediation proposals - deliberately *not* a model-selectable tool (D45).

``execute_fix`` never touches the database or the fleet: it validates the action
and returns a proposal that a human approves.  Registering it in ``TOOLS`` would
hand a hallucinating model write access to production-shaped infrastructure.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FixAction = Literal["drain", "cordon", "restart_bmc", "replace_gpu"]


class FixArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hostname: str = Field(pattern=r"^gpu-\d{3}$")
    action: FixAction
    reason: str = Field(min_length=4, max_length=255)


def execute_fix(hostname: str, action: FixAction, reason: str) -> dict[str, Any]:
    """Build an approval request for ``action`` on ``hostname``; never executes it."""
    args = FixArgs(hostname=hostname, action=action, reason=reason)
    return {
        "status": "proposed",
        "requires_approval": True,
        "hostname": args.hostname,
        "action": args.action,
        "reason": args.reason,
    }
