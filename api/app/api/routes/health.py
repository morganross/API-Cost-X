"""
Health Check and System Information Endpoints
"""
from fastapi import APIRouter

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check():
    """Health check endpoint for local monitoring."""
    return {
        "status": "healthy",
        "version": "1.0.0",
        "service": "APICostX API"
    }


@router.get("/health/safe-to-restart")
async def safe_to_restart():
    """
    Return whether it is safe to restart the service.

    The response tells restart scripts (restart.sh) whether any runs are
    currently being executed in-process.  If active_runs > 0 the caller
    should either wait or pass --force.

    We use the in-memory _active_executors dict from the execution module
    rather than the database because a process crash can leave DB rows
    stuck in RUNNING; the dict is always accurate for the current process.
    """
    try:
        from app.api.routes.runs.execution import _active_executors
        active_count = len(_active_executors)
    except Exception:
        # If import fails for any reason, err on the side of caution
        active_count = -1

    return {
        "safe": active_count == 0,
        "active_runs": active_count,
    }


@router.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "service": "APICostX - AI Content Model Evaluation System",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health"
    }
