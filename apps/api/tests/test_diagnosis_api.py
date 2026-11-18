"""D49: alert intake + diagnosis run records over HTTP."""

from __future__ import annotations

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.models.diagnosis import Alert, DiagnosisRun, DiagnosisStep, IncidentNode
from tests.conftest import API, USER_EMAIL, USER_PASSWORD, auth_headers


@pytest_asyncio.fixture
async def node_id(session_factory, seeded) -> int:
    async with session_factory() as db:
        node = IncidentNode(
            hostname="gpu-003",
            node_model="DGX-A100",
            switch="sw-01",
            rack="rack-01",
            gpu_model="A100-80G",
            gpu_count=8,
        )
        db.add(node)
        await db.commit()
        return node.id


def _alert_payload(node_id: int | None = None, **overrides) -> dict:
    body = {
        "node_id": node_id,
        "script_key": "gpu-ecc-01",
        "severity": "critical",
        "summary": "GPU2 ECC error count rising on gpu-003",
    }
    body.update(overrides)
    return body


async def test_create_alert_records_an_open_alert(client: AsyncClient, node_id: int) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    response = await client.post(
        f"{API}/alerts",
        json=_alert_payload(node_id, fired_at="2026-11-17T09:00:00Z"),
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"] > 0
    assert body["node_id"] == node_id
    assert body["script_key"] == "gpu-ecc-01"
    assert body["severity"] == "critical"
    assert body["status"] == "open"
    assert body["fired_at"].startswith("2026-11-17T09:00:00")


async def test_alert_for_unknown_node_is_404(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    response = await client.post(f"{API}/alerts", json=_alert_payload(9999), headers=headers)
    assert response.status_code == 404
    assert response.json()["detail"] == "Incident node not found"


async def test_alert_requires_an_authenticated_caller(client: AsyncClient) -> None:
    response = await client.post(f"{API}/alerts", json=_alert_payload())
    assert response.status_code == 401


async def test_alert_payload_is_validated(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    response = await client.post(f"{API}/alerts", json=_alert_payload(summary=""), headers=headers)
    assert response.status_code == 422


async def test_start_diagnosis_is_single_flight(
    client: AsyncClient, session_factory, node_id: int
) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    alert = (
        await client.post(f"{API}/alerts", json=_alert_payload(node_id), headers=headers)
    ).json()

    response = await client.post(
        f"{API}/diagnosis", json={"alert_id": alert["id"]}, headers=headers
    )
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["alert_id"] == alert["id"]
    assert run["status"] == "pending"
    assert run["steps"] == []
    assert run["tokens"] == 0

    async with session_factory() as db:
        stored = await db.get(Alert, alert["id"])
        assert stored.status == "diagnosing"
        assert await db.scalar(select(DiagnosisRun.id).where(DiagnosisRun.id == run["id"]))

    # A second start would give the alert two investigators - rejected.
    duplicate = await client.post(
        f"{API}/diagnosis", json={"alert_id": alert["id"]}, headers=headers
    )
    assert duplicate.status_code == 409


async def test_diagnosis_for_unknown_alert_is_404(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    response = await client.post(f"{API}/diagnosis", json={"alert_id": 12345}, headers=headers)
    assert response.status_code == 404
    assert response.json()["detail"] == "Alert not found"


async def test_get_diagnosis_returns_the_evidence_chain(
    client: AsyncClient, session_factory, node_id: int
) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    alert = (
        await client.post(f"{API}/alerts", json=_alert_payload(node_id), headers=headers)
    ).json()
    run = (
        await client.post(f"{API}/diagnosis", json={"alert_id": alert["id"]}, headers=headers)
    ).json()

    # The agent (M6) appends steps out of order; the API must replay by seq.
    async with session_factory() as db:
        db.add_all(
            [
                DiagnosisStep(
                    run_id=run["id"],
                    seq=2,
                    node="tool",
                    tool_name="get_node_gpus",
                    args={"pattern": "gpu-003"},
                    result={"faulty_cards": [2]},
                    latency_ms=7,
                ),
                DiagnosisStep(run_id=run["id"], seq=1, node="plan", latency_ms=1),
            ]
        )
        await db.commit()

    response = await client.get(f"{API}/diagnosis/{run['id']}", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [step["seq"] for step in body["steps"]] == [1, 2]
    assert body["steps"][1]["tool_name"] == "get_node_gpus"
    assert body["steps"][1]["result"] == {"faulty_cards": [2]}
    assert body["steps"][1]["latency_ms"] == 7


async def test_get_unknown_diagnosis_is_404(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    response = await client.get(f"{API}/diagnosis/987654", headers=headers)
    assert response.status_code == 404
    assert response.json()["detail"] == "Diagnosis run not found"
