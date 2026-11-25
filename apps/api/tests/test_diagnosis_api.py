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


async def _open_run(
    client: AsyncClient, headers: dict, node_id: int, summary: str
) -> dict[str, int]:
    alert = (
        await client.post(
            f"{API}/alerts",
            json=_alert_payload(node_id, summary=summary),
            headers=headers,
        )
    ).json()
    run = (
        await client.post(f"{API}/diagnosis", json={"alert_id": alert["id"]}, headers=headers)
    ).json()
    return {"alert_id": alert["id"], "run_id": run["id"]}


async def test_list_diagnosis_flattens_the_alert(
    client: AsyncClient, session_factory, node_id: int
) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    first = await _open_run(client, headers, node_id, "ECC 错误")
    second = await _open_run(client, headers, node_id, "NCCL 超时")

    # D55 shows the conclusion in the list, so the agent's verdict must surface.
    async with session_factory() as db:
        run = await db.get(DiagnosisRun, first["run_id"])
        run.status = "succeeded"
        run.root_cause = "GPU2 ECC 不可纠正"
        run.confidence = 0.86
        run.iterations = 3
        run.tokens = 1200
        run.duration_ms = 4200
        await db.commit()

    response = await client.get(f"{API}/diagnosis", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [second["run_id"], first["run_id"]]

    concluded = body["items"][1]
    assert concluded["alert_summary"] == "ECC 错误"
    assert concluded["alert_severity"] == "critical"
    assert concluded["status"] == "succeeded"
    assert concluded["root_cause"] == "GPU2 ECC 不可纠正"
    assert concluded["confidence"] == 0.86
    assert concluded["iterations"] == 3
    assert concluded["tokens"] == 1200
    assert concluded["duration_ms"] == 4200
    assert body["items"][0]["root_cause"] is None


async def test_list_diagnosis_filters_and_paginates(
    client: AsyncClient, session_factory, node_id: int
) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    first = await _open_run(client, headers, node_id, "ECC 错误")
    await _open_run(client, headers, node_id, "NCCL 超时")

    async with session_factory() as db:
        run = await db.get(DiagnosisRun, first["run_id"])
        run.status = "failed"
        await db.commit()

    filtered = await client.get(f"{API}/diagnosis?status=failed", headers=headers)
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["id"] == first["run_id"]

    page = await client.get(f"{API}/diagnosis?limit=1&skip=1", headers=headers)
    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert len(page.json()["items"]) == 1


async def test_list_diagnosis_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get(f"{API}/diagnosis")).status_code == 401


async def test_list_diagnosis_rejects_bad_pagination(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    assert (await client.get(f"{API}/diagnosis?limit=0", headers=headers)).status_code == 422
    assert (await client.get(f"{API}/diagnosis?limit=201", headers=headers)).status_code == 422
    assert (await client.get(f"{API}/diagnosis?status=bogus", headers=headers)).status_code == 422
