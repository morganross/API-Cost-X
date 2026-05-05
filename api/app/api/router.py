"""
API Router - Combines all route modules.
"""
from fastapi import APIRouter

from .routes import (
    runs, presets, models,
    contents, github_connections, health,
    settings
)

# Single clean API router
api_router = APIRouter(prefix="/api")

def _register_common_routes(router: APIRouter) -> None:
    router.include_router(runs.router)
    router.include_router(presets.router)
    router.include_router(models.router)
    router.include_router(contents.router)
    router.include_router(github_connections.router)
    router.include_router(health.router)
    router.include_router(settings.router)


# Include all route modules
_register_common_routes(api_router)
