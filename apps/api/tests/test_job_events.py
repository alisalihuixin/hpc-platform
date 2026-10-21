"""SSE job events (F4.7): publishing, filtering and transition wiring."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.core.events import JOB_EVENTS_CHANNEL, job_event_stream, publish_job_event
from app.models.group import Group
from app.models.job import Job
from app.models.node import Node
from app.models.quota import Quota
from app.models.user import User
from app.services import scheduler_service
from tests.conftest import API, USER_EMAIL, USER_PASSWORD, auth_headers


@pytest_asyncio.fixture
async def team(session_factory, seeded) -> int:
    async with session_factory() as db:
        user = await db.scalar(select(User).where(User.email == USER_EMAIL))
        group = Group(name="team-sse")
        group.members.append(user)
        db.add(group)
        await db.flush()
        db.add(Quota(group_id=group.id, gpu_limit=8, max_nodes_per_job=2, max_gpus_per_job=4))
        await db.commit()
        return group.id


async def _add_node(session_factory, *, gpu_total: int = 4) -> int:
    async with session_factory() as db:
        node = Node(hostname="gpu-sse", cpu_cores=32, mem_gb=128, gpu_total=gpu_total)
        db.add(node)
        await db.commit()
        return node.id


async def _read_message(pubsub, timeout: float = 2.0) -> dict:
    async def _loop() -> dict:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.05)
            if message is not None:
                return message
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(_loop(), timeout=timeout)


async def _read_event(stream, timeout: float = 2.0) -> dict:
    async def _loop() -> dict:
        while True:
            frame = await anext(stream)
            if "job.updated" in frame:
                return json.loads(frame.split("data: ", 1)[1])

    return await asyncio.wait_for(_loop(), timeout=timeout)


# --------------------------------------------------------------- core plumbing


async def test_publish_delivers_to_subscribers(redis_client) -> None:
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(JOB_EVENTS_CHANNEL)
    try:
        await publish_job_event(redis_client, job_id=42, user_id=7, status="queued")
        payload = json.loads((await _read_message(pubsub))["data"])
    finally:
        await pubsub.aclose()

    assert payload["type"] == "job.updated"
    assert payload["job_id"] == 42
    assert payload["user_id"] == 7
    assert payload["status"] == "queued"
    assert "at" in payload


async def test_publish_without_redis_is_a_noop() -> None:
    await publish_job_event(None, job_id=1, user_id=1, status="queued")


async def test_stream_filters_other_users(redis_client) -> None:
    stream = job_event_stream(redis_client, user_id=7, is_admin=False, heartbeat_seconds=0.05)
    try:
        assert await anext(stream) == ": connected\n\n"
        await publish_job_event(redis_client, job_id=1, user_id=8, status="running")
        await publish_job_event(redis_client, job_id=2, user_id=7, status="running")
        payload = await _read_event(stream)
    finally:
        await stream.aclose()

    assert payload["job_id"] == 2
    assert payload["user_id"] == 7


async def test_admin_stream_receives_every_user(redis_client) -> None:
    stream = job_event_stream(redis_client, user_id=99, is_admin=True, heartbeat_seconds=0.05)
    try:
        assert await anext(stream) == ": connected\n\n"
        await publish_job_event(redis_client, job_id=5, user_id=8, status="failed")
        payload = await _read_event(stream)
    finally:
        await stream.aclose()

    assert payload["job_id"] == 5
    assert payload["status"] == "failed"


async def test_stream_endpoint_requires_auth(client: AsyncClient, seeded) -> None:
    assert (await client.get(f"{API}/jobs/stream")).status_code == 401


# ------------------------------------------------------- transition publishing


async def test_submit_publishes_queued_event(
    client: AsyncClient, seeded, team, redis_client
) -> None:
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(JOB_EVENTS_CHANNEL)
    try:
        headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
        response = await client.post(
            f"{API}/jobs",
            headers=headers,
            json={
                "group_id": team,
                "name": "sse-job",
                "num_nodes": 1,
                "num_gpus": 2,
                "walltime_min": 60,
                "params": {},
            },
        )
        assert response.status_code == 201, response.text
        created = response.json()
        payload = json.loads((await _read_message(pubsub))["data"])
    finally:
        await pubsub.aclose()

    assert payload["job_id"] == created["id"]
    assert payload["user_id"] == created["user_id"]
    assert payload["status"] == "queued"


async def test_running_and_cancel_publish_and_release_node(
    client: AsyncClient, seeded, team, session_factory, redis_client
) -> None:
    node_id = await _add_node(session_factory)
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(JOB_EVENTS_CHANNEL)
    try:
        headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
        created = await client.post(
            f"{API}/jobs",
            headers=headers,
            json={
                "group_id": team,
                "name": "sse-cancel",
                "num_nodes": 1,
                "num_gpus": 2,
                "walltime_min": 60,
                "params": {},
            },
        )
        job_id = created.json()["id"]

        async with session_factory() as db:
            assert await scheduler_service.schedule_once(db, redis=redis_client) == 1

        canceled = await client.post(f"{API}/jobs/{job_id}/cancel", headers=headers)
        assert canceled.status_code == 200, canceled.text

        events = [json.loads((await _read_message(pubsub))["data"]) for _ in range(3)]
    finally:
        await pubsub.aclose()

    assert [event["status"] for event in events] == ["queued", "running", "canceled"]
    assert {event["job_id"] for event in events} == {job_id}

    async with session_factory() as db:
        assert (await db.get(Node, node_id)).gpu_used == 0


async def test_finish_publishes_terminal_event(session_factory, seeded, team, redis_client) -> None:
    started = datetime(2026, 1, 1, 10, 0, 0)
    async with session_factory() as db:
        user = await db.scalar(select(User).where(User.email == USER_EMAIL))
        node = Node(hostname="gpu-sse-finish", cpu_cores=32, mem_gb=128, gpu_total=4, gpu_used=2)
        db.add(node)
        await db.flush()
        db.add(
            Job(
                user_id=user.id,
                group_id=team,
                name="sse-finish",
                num_nodes=1,
                num_gpus=2,
                walltime_min=60,
                params={},
                status="running",
                node_id=node.id,
                started_at=started,
            )
        )
        await db.commit()

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(JOB_EVENTS_CHANNEL)
    try:
        async with session_factory() as db:
            assert (
                await scheduler_service.finish_due_jobs(
                    db, now=started + timedelta(minutes=61), redis=redis_client
                )
                == 1
            )
        payload = json.loads((await _read_message(pubsub))["data"])
    finally:
        await pubsub.aclose()

    assert payload["status"] == "completed"
    assert payload["user_id"] == user.id
