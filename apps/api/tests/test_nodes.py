"""Node domain (F2): CRUD, RBAC, status transitions and the GPU guard."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    API,
    USER_EMAIL,
    USER_PASSWORD,
    auth_headers,
)

NODE = {
    "hostname": "gpu-01",
    "cpu_cores": 64,
    "mem_gb": 512,
    "gpu_model": "A100-80G",
    "gpu_total": 8,
    "tags": {"rack": "r1"},
}


async def _create_node(client: AsyncClient, headers: dict[str, str], **overrides) -> dict:
    response = await client.post(f"{API}/nodes", headers=headers, json={**NODE, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


async def test_anonymous_cannot_list_nodes(client: AsyncClient, seeded) -> None:
    assert (await client.get(f"{API}/nodes")).status_code == 401


async def test_admin_creates_and_reads_node(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    body = await _create_node(client, headers)

    assert body["hostname"] == "gpu-01"
    assert body["status"] == "online"
    assert body["gpu_used"] == 0
    assert body["gpu_free"] == 8
    assert body["tags"] == {"rack": "r1"}

    fetched = await client.get(f"{API}/nodes/{body['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["hostname"] == "gpu-01"


async def test_standard_user_can_view_but_not_manage(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    assert (await client.get(f"{API}/nodes", headers=headers)).status_code == 200
    assert (await client.post(f"{API}/nodes", headers=headers, json=NODE)).status_code == 403


async def test_duplicate_hostname_conflicts(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    await _create_node(client, headers)
    duplicate = await client.post(f"{API}/nodes", headers=headers, json=NODE)
    assert duplicate.status_code == 409


async def test_updating_hostname_to_an_existing_one_conflicts(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    first = await _create_node(client, headers)
    second = await _create_node(client, headers, hostname="gpu-02")

    clash = await client.patch(
        f"{API}/nodes/{second['id']}", headers=headers, json={"hostname": first["hostname"]}
    )
    assert clash.status_code == 409


async def test_node_can_be_drained_and_filtered(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    first = await _create_node(client, headers)
    second = await _create_node(client, headers, hostname="gpu-02")

    drained = await client.patch(
        f"{API}/nodes/{second['id']}", headers=headers, json={"status": "draining"}
    )
    assert drained.status_code == 200
    assert drained.json()["status"] == "draining"

    online = await client.get(f"{API}/nodes", headers=headers, params={"status": "online"})
    assert online.json()["total"] == 1
    assert online.json()["items"][0]["id"] == first["id"]

    draining = await client.get(f"{API}/nodes", headers=headers, params={"status": "draining"})
    assert draining.json()["total"] == 1


async def test_gpu_used_is_guarded_by_total(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    node = await _create_node(client, headers)

    # Only gpu_used is sent, so the service compares it against the stored total.
    over = await client.patch(f"{API}/nodes/{node['id']}", headers=headers, json={"gpu_used": 99})
    assert over.status_code == 400

    # Both sides in one payload are caught by schema validation.
    combined = await client.patch(
        f"{API}/nodes/{node['id']}", headers=headers, json={"gpu_used": 9, "gpu_total": 8}
    )
    assert combined.status_code == 422


async def test_gpu_used_can_be_set_within_total(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    node = await _create_node(client, headers)

    updated = await client.patch(f"{API}/nodes/{node['id']}", headers=headers, json={"gpu_used": 3})
    assert updated.status_code == 200
    assert updated.json()["gpu_used"] == 3
    assert updated.json()["gpu_free"] == 5


async def test_delete_node_then_404(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    node = await _create_node(client, headers)

    assert (await client.delete(f"{API}/nodes/{node['id']}", headers=headers)).status_code == 204
    assert (await client.get(f"{API}/nodes/{node['id']}", headers=headers)).status_code == 404
    assert (await client.delete(f"{API}/nodes/{node['id']}", headers=headers)).status_code == 404


async def test_standard_user_cannot_delete(client: AsyncClient, seeded) -> None:
    admin_headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    node = await _create_node(client, admin_headers)

    user_headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    assert (
        await client.delete(f"{API}/nodes/{node['id']}", headers=user_headers)
    ).status_code == 403
