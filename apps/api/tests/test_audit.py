"""Audit trail (F7.1 / F7.3).

The triggers are installed by the pytest bootstrap with the same DDL the
migration uses, so these cases exercise real database behaviour rather than an
application-side stub.
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import func, select, update

from app.models.audit_log import AuditLog
from app.models.group import Group
from app.models.job import Job
from app.models.quota import Quota
from app.models.user import User
from tests.conftest import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    API,
    USER_EMAIL,
    USER_PASSWORD,
    auth_headers,
)

NODE = {
    "hostname": "gpu-audit",
    "cpu_cores": 64,
    "mem_gb": 512,
    "gpu_model": "A100-80G",
    "gpu_total": 8,
}


async def test_node_update_and_delete_are_audited(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)

    created = await client.post(f"{API}/nodes", headers=headers, json=NODE)
    node_id = created.json()["id"]
    # INSERT is not audited - only UPDATE / DELETE.
    assert (await client.get(f"{API}/audit-logs?table_name=nodes", headers=headers)).json()[
        "total"
    ] == 0

    await client.patch(f"{API}/nodes/{node_id}", headers=headers, json={"gpu_total": 16})
    assert (await client.delete(f"{API}/nodes/{node_id}", headers=headers)).status_code == 204

    body = (
        await client.get(f"{API}/audit-logs?table_name=nodes&record_id={node_id}", headers=headers)
    ).json()

    assert body["total"] == 2
    by_action = {item["action"]: item for item in body["items"]}
    assert set(by_action) == {"update", "delete"}
    assert by_action["update"]["before"]["gpu_total"] == 8
    assert by_action["update"]["after"]["gpu_total"] == 16
    assert by_action["delete"]["after"] is None
    assert by_action["delete"]["before"]["hostname"] == "gpu-audit"


async def test_user_password_hash_is_never_stored(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    listing = (await client.get(f"{API}/users", headers=headers)).json()["items"]
    user_id = next(item["id"] for item in listing if item["email"] == USER_EMAIL)

    patched = await client.patch(
        f"{API}/users/{user_id}",
        headers=headers,
        json={"password": "reset-pass-123", "is_active": False},
    )
    assert patched.status_code == 200, patched.text

    body = (
        await client.get(f"{API}/audit-logs?table_name=users&record_id={user_id}", headers=headers)
    ).json()

    assert body["total"] >= 1
    latest = body["items"][0]
    assert latest["action"] == "update"
    assert "hashed_password" not in latest["before"]
    assert "hashed_password" not in latest["after"]
    # SQLite's json_object renders booleans as 0/1; either way it must be falsy.
    assert not latest["after"]["is_active"]


async def test_job_audit_only_fires_when_status_moves(session_factory, seeded) -> None:
    async with session_factory() as db:
        owner = await db.scalar(select(User).where(User.email == USER_EMAIL))
        group = Group(name="team-audit")
        group.members.append(owner)
        db.add(group)
        await db.flush()
        db.add(Quota(group_id=group.id, gpu_limit=4, max_nodes_per_job=1, max_gpus_per_job=2))
        job = Job(
            user_id=owner.id,
            group_id=group.id,
            name="audit-job",
            num_nodes=1,
            num_gpus=1,
            walltime_min=10,
            params={},
            status="queued",
        )
        db.add(job)
        await db.commit()
        job_id = job.id

    async with session_factory() as db:
        await db.execute(update(Job).where(Job.id == job_id).values(walltime_min=20))
        await db.commit()
        counted = await db.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.table_name == "jobs")
        )
        assert counted == 0

    async with session_factory() as db:
        await db.execute(update(Job).where(Job.id == job_id).values(status="running"))
        await db.commit()
        rows = list((await db.scalars(select(AuditLog).where(AuditLog.table_name == "jobs"))).all())

    assert len(rows) == 1
    assert rows[0].record_id == job_id
    assert rows[0].before["status"] == "queued"
    assert rows[0].after["status"] == "running"


async def test_audit_endpoint_requires_admin_and_validates_query(
    client: AsyncClient, seeded
) -> None:
    assert (await client.get(f"{API}/audit-logs")).status_code == 401

    user_headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    assert (await client.get(f"{API}/audit-logs", headers=user_headers)).status_code == 403

    admin_headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    assert (
        await client.get(f"{API}/audit-logs?action=nope", headers=admin_headers)
    ).status_code == 422
    assert (await client.get(f"{API}/audit-logs?limit=0", headers=admin_headers)).status_code == 422
