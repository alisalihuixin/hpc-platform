"""Node endpoints (F2).

Read endpoints (F2.3 resource view) are open to any signed-in user; the
create/update/delete endpoints are admin-only (F2.2).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.errors import NotFoundError
from app.deps import CurrentUser, SessionDep, require_roles
from app.models.node import NodeStatus
from app.schemas.node import NodeCreate, NodeList, NodeRead, NodeUpdate
from app.services import node_service

router = APIRouter(prefix="/nodes", tags=["nodes"])

admin_only = [Depends(require_roles("admin"))]


@router.get("", response_model=NodeList, summary="List nodes (read-only resource view)")
async def list_nodes(
    db: SessionDep,
    _user: CurrentUser,
    status_filter: Annotated[
        NodeStatus | None, Query(alias="status", description="Filter by node status")
    ] = None,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> NodeList:
    nodes, total = await node_service.list_nodes(
        db, skip=skip, limit=limit, status=status_filter.value if status_filter else None
    )
    return NodeList(items=[NodeRead.model_validate(node) for node in nodes], total=total)


@router.post(
    "",
    response_model=NodeRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=admin_only,
    summary="Register a node (admin)",
)
async def create_node(payload: NodeCreate, db: SessionDep) -> NodeRead:
    node = await node_service.create_node(db, payload)
    return NodeRead.model_validate(node)


@router.get("/{node_id}", response_model=NodeRead, summary="Get a node")
async def get_node(node_id: int, db: SessionDep, _user: CurrentUser) -> NodeRead:
    node = await node_service.get_node(db, node_id)
    if node is None:
        raise NotFoundError("Node not found")
    return NodeRead.model_validate(node)


@router.patch(
    "/{node_id}",
    response_model=NodeRead,
    dependencies=admin_only,
    summary="Update a node, e.g. take it offline / drain it (admin)",
)
async def update_node(node_id: int, payload: NodeUpdate, db: SessionDep) -> NodeRead:
    node = await node_service.update_node(db, node_id, payload)
    return NodeRead.model_validate(node)


@router.delete(
    "/{node_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=admin_only,
    summary="Delete a node (admin)",
)
async def delete_node(node_id: int, db: SessionDep) -> None:
    await node_service.delete_node(db, node_id)
