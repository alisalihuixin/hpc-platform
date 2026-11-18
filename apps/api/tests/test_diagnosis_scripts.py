"""D43: the 20 scripts, their 5 traps, and the alert/eval-case alignment."""

from __future__ import annotations

from sqlalchemy import select

from app.diagnosis.data.mock import build_fleet, seed_diagnosis_data
from app.diagnosis.data.scripts import (
    EVIDENCE_TOOLS,
    SCRIPTS,
    seed_fault_scripts,
)
from app.models.diagnosis import Alert, EvalCase, IncidentNode


def test_catalogue_has_20_scripts_and_5_traps() -> None:
    assert len(SCRIPTS) == 20
    assert len({script.key for script in SCRIPTS}) == 20
    # The eval docstring pins the trap positions; D57 reports them separately.
    assert [index + 1 for index, script in enumerate(SCRIPTS) if script.trap] == [7, 12, 17, 18, 20]
    traps = [script for script in SCRIPTS if script.trap]
    assert len(traps) == 5
    for script in traps:
        assert script.misdirection
        assert script.trap_note
        assert script.misdirection != script.expected_cause
    for script in SCRIPTS:
        if not script.trap:
            assert script.misdirection is None
            assert script.trap_note is None


def test_scripts_reference_seeded_nodes_and_known_tools() -> None:
    fleet = build_fleet()
    hostnames = {node.hostname for node in fleet.nodes}
    for script in SCRIPTS:
        assert len(script.key) <= 64
        assert len(script.title) <= 512
        assert len(script.expected_cause) <= 255
        assert set(script.hostnames) <= hostnames
        assert script.expected_evidence
        for item in script.expected_evidence:
            assert item["tool"] in EVIDENCE_TOOLS
            assert item["args"]
    assert "execute_fix" not in EVIDENCE_TOOLS


def test_switch_and_rack_scripts_cover_whole_racks() -> None:
    fleet = build_fleet()
    by_hostname = {node.hostname: node for node in fleet.nodes}
    for script in SCRIPTS:
        if len(script.hostnames) < 5:
            continue
        switches = {by_hostname[hostname].switch for hostname in script.hostnames}
        racks = {by_hostname[hostname].rack for hostname in script.hostnames}
        # The multi-node scripts are the M7 correlation demo: one switch == one rack.
        assert len(switches) == 1 and len(racks) == 1


async def test_seed_links_script_alert_and_ground_truth(session_factory) -> None:
    async with session_factory() as db:
        await seed_diagnosis_data(db)
        report = await seed_fault_scripts(db)
        await db.commit()

    assert report.inserted == 20
    assert report.eval_cases == 20
    # 200 historical tickets + one alert per script.
    assert report.alerts == 220

    async with session_factory() as db:
        cases = {case.script_key: case for case in (await db.scalars(select(EvalCase))).all()}
        alerts = {
            alert.script_key: alert
            for alert in (
                await db.scalars(select(Alert).where(Alert.script_key.in_(list(cases))))
            ).all()
        }
        nodes = {node.id: node.hostname for node in (await db.scalars(select(IncidentNode))).all()}

    assert set(alerts) == set(cases) == {script.key for script in SCRIPTS}
    assert len({alert.fired_at for alert in alerts.values()}) == 20

    for script in SCRIPTS:
        case, alert = cases[script.key], alerts[script.key]
        assert case.expected_cause == script.expected_cause
        assert case.trap is script.trap
        assert case.notes == script.trap_note
        assert case.expected_evidence == [dict(item) for item in script.expected_evidence]
        assert alert.summary == script.title
        assert alert.severity == script.severity.value
        assert alert.status == "open"
        if script.hostnames:
            assert nodes[alert.node_id] == script.hostnames[0]
        else:
            assert alert.node_id is None


async def test_second_script_seed_is_noop(session_factory) -> None:
    async with session_factory() as db:
        await seed_diagnosis_data(db)
        await seed_fault_scripts(db)
        await db.commit()

    async with session_factory() as db:
        report = await seed_fault_scripts(db)
        await db.commit()
        assert report.inserted == 0
        assert report.alerts == 220
        assert report.eval_cases == 20
