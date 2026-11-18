"""D46: replay every script's expected evidence through the real tool layer.

If a script asks for evidence no tool can return, the evaluation would punish the
agent for a data gap.  This walks all 20 scripts in order (56 tool calls) and
fails on the first missing/empty result, so D57 measures reasoning, not plumbing.
"""

from __future__ import annotations

from typing import Any

from app.diagnosis.data.mock import seed_diagnosis_data
from app.diagnosis.data.runbooks import seed_runbooks
from app.diagnosis.data.scripts import SCRIPTS, seed_fault_scripts
from app.diagnosis.tools import TOOLS, ToolResponse, get_node_status


async def _seeded(session_factory) -> None:
    async with session_factory() as db:
        await seed_diagnosis_data(db)
        await seed_fault_scripts(db)
        await seed_runbooks(db)
        await db.commit()


async def _invoke(db, tool: str, args: dict[str, Any]) -> ToolResponse:
    match tool:
        case "get_node_status":
            return await TOOLS[tool](db, args["hostname"])
        case "get_node_gpus":
            return await TOOLS[tool](db, args["hostname"])
        case "get_job_logs":
            return await TOOLS[tool](db, args["hostname"], limit=args.get("limit", 50))
        case "get_metrics":
            return await TOOLS[tool](db, window=args.get("window", 60))
        case "search_runbooks":
            return await TOOLS[tool](db, tags=args.get("tags"), query=args.get("query"))
        case _:  # pragma: no cover - guards a typo in the catalogue
            raise AssertionError(f"script asks for unknown tool {tool}")


async def test_all_20_scripts_have_retrievable_evidence(session_factory) -> None:
    await _seeded(session_factory)

    failures: list[str] = []
    checked = 0
    async with session_factory() as db:
        for script in SCRIPTS:
            for item in script.expected_evidence:
                response = await _invoke(db, item["tool"], item["args"])
                checked += 1
                if not response.ok:
                    failures.append(f"{script.key}: {item['tool']} -> {response.error}")
                elif (
                    item["tool"] == "search_runbooks" and (response.data or {}).get("count", 0) == 0
                ):
                    failures.append(
                        f"{script.key}: {item['tool']} matched no runbook for {item['args']}"
                    )

    assert checked == sum(len(script.expected_evidence) for script in SCRIPTS) == 56
    assert failures == []
    assert len(SCRIPTS) == 20


async def test_multi_node_scripts_stay_on_one_switch(session_factory) -> None:
    await _seeded(session_factory)
    multi_node = [script for script in SCRIPTS if len(script.hostnames) >= 5]
    # Traps 7/18 plus the switch/PDU scripts (9/10) span a whole rack.
    assert len(multi_node) == 4

    async with session_factory() as db:
        for script in multi_node:
            switches = set()
            for hostname in script.hostnames:
                response = await get_node_status(db, hostname)
                assert response.ok and response.data is not None
                switches.add(response.data["switch"])
            # The M7 correlation demo depends on this: one switch, five nodes.
            assert len(switches) == 1
