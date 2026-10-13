"""API routers.  ``api_router`` is mounted under ``/api/v1``."""

from fastapi import APIRouter

from app.routers import auth, health, nodes, users

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(nodes.router)

health_router = health.router

__all__ = ["api_router", "health_router"]
