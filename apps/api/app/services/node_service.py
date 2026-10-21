"""Node management use-cases (F2)."""

from __future__ import annotations

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ConflictError, NotFoundError
from app.models.node import Node
from app.schemas.node import NodeCreate, NodeUpdate


async def get_node(db: AsyncSession, node_id: int) -> Node | None:
    return await db.get(Node, node_id)


async def list_nodes(
    db: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 50,
    status: str | None = None,
) -> tuple[list[Node], int]:
    conditions = [Node.status == status] if status is not None else []
    total = await db.scalar(select(func.count()).select_from(Node).where(*conditions)) or 0
    rows = await db.scalars(
        select(Node).where(*conditions).order_by(Node.hostname).offset(skip).limit(limit)
    )
    return list(rows.all()), int(total)


async def create_node(db: AsyncSession, payload: NodeCreate) -> Node:
    existing = await db.scalar(select(Node).where(Node.hostname == payload.hostname))
    if existing is not None:
        raise ConflictError("Hostname is already registered")

    node = Node(**payload.model_dump())
    db.add(node)
    await db.commit()
    await db.refresh(node)
    return node


async def update_node(db: AsyncSession, node_id: int, payload: NodeUpdate) -> Node:
    node = await get_node(db, node_id)
    if node is None:
        raise NotFoundError("Node not found")

    changes = payload.model_dump(exclude_unset=True, mode="json")

    new_hostname = changes.get("hostname")
    if new_hostname is not None and new_hostname != node.hostname:
        clash = await db.scalar(select(Node).where(Node.hostname == new_hostname))
        if clash is not None:
            raise ConflictError("Hostname is already registered")

    for field, value in changes.items():
        setattr(node, field, value)

    # Cross-field guard for updates that only touch one side of the pair.
    if node.gpu_used > node.gpu_total:
        raise AppError("gpu_used cannot exceed gpu_total")

    await db.commit()
    await db.refresh(node)
    return node


async def delete_node(db: AsyncSession, node_id: int) -> None:
    node = await get_node(db, node_id)
    if node is None:
        raise NotFoundError("Node not found")
    await db.delete(node)
    await db.commit()


async def release_slots(
    db: AsyncSession,
    *,
    node_id: int | None,
    num_gpus: int,
    num_cpus: int,
    mem_gb: int,
) -> None:
    """Return a finished job's slots, clamped at zero so counters never underflow.

    Used by the scheduler (terminal transitions) and by cancellation.
    """
    if node_id is None:
        return
    await db.execute(
        update(Node)
        .where(Node.id == node_id)
        .values(
            gpu_used=case((Node.gpu_used >= num_gpus, Node.gpu_used - num_gpus), else_=0),
            cpu_used=case((Node.cpu_used >= num_cpus, Node.cpu_used - num_cpus), else_=0),
            mem_used=case((Node.mem_used >= mem_gb, Node.mem_used - mem_gb), else_=0),
        )
    )
