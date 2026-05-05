"""
APICostX Self-Hosted

FastAPI application for local research evaluation.
"""
import asyncio
import logging
import logging.handlers
import os
import sys
import traceback
from contextlib import asynccontextmanager, suppress
from ipaddress import ip_address
from pathlib import Path
from time import perf_counter

# Windows requires ProactorEventLoop for subprocess support (used by FPF adapter)
if sys.platform == 'win32':
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware

from .api.router import api_router
from .infra.db.session import engine
from .infra.db.models import Base
from .infra.db.repositories import PresetRepository, DocumentRepository, RunRepository
from .config import get_settings
from .auth.user_registry import load_registry
from .seed.base_database import initialize_main_database_from_base
# Routes use a local single-user dependency; there is no account system.


class NoCacheMiddleware(BaseHTTPMiddleware):
    """
    Middleware to disable ALL caching on API responses.

    CRITICAL: Caching has caused 3 years of "works once, never again" bugs.
    Old cached responses mask code changes, causing developers to stare at
    new code while the browser/proxy serves stale data.

    This middleware adds aggressive no-cache headers to EVERY response.
    """
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        # Disable all caching
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
        response.headers["X-Accel-Expires"] = "0"  # nginx
        response.headers["Surrogate-Control"] = "no-store"  # CDNs
        return response


class TrailingSlashMiddleware(BaseHTTPMiddleware):
    """
    Middleware to normalize trailing slashes.

    Strips trailing slashes from all requests (except root "/") BEFORE routing.
    This ensures both /api/path and /api/path/ work
    without 307 redirects.

    Combined with redirect_slashes=False on the FastAPI app, this handles
    any slash variation consistently.
    """
    async def dispatch(self, request: Request, call_next):
        # Strip trailing slash from path (except for root "/")
        path = request.scope["path"]
        if path != "/" and path.endswith("/"):
            request.scope["path"] = path.rstrip("/")
        return await call_next(request)


class DocsSecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Apply browser hardening headers to the public FastAPI docs pages only.

    These pages load third-party docs assets from jsDelivr, Google Fonts,
    and FastAPI's favicon host. We keep the
    policy scoped to /docs, /docs/oauth2-redirect, and /redoc so API responses
    and normal API responses are unaffected.
    """

    DOC_PATHS = {"/docs", "/docs/oauth2-redirect", "/redoc"}
    DOCS_CSP = "; ".join(
        [
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "form-action 'self'",
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://static.cloudflareinsights.com",
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com",
            "img-src 'self' data: https://fastapi.tiangolo.com",
            "font-src 'self' data: https://fonts.gstatic.com",
            "connect-src 'self'",
            "frame-src 'self'",
        ]
    )

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path in self.DOC_PATHS:
            response.headers["Content-Security-Policy"] = self.DOCS_CSP
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response


# Configure logging with BOTH console AND file output
# This fixes the gap where APICostX main process logs were console-only
_log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_log_dir = Path(__file__).parent.parent / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)

# Generate timestamped log filename: apicostx_main_YYYYMMDD_HHMMSS.log
from datetime import datetime as _dt
_log_timestamp = _dt.now().strftime("%Y%m%d_%H%M%S")
_log_file = _log_dir / f"apicostx_main_{_log_timestamp}.log"

# Custom namer for rotated files to include timestamp
def _timestamped_namer(default_name: str) -> str:
    """Rename rotated logs with timestamp: apicostx_main_20260209_120000.log.1 -> apicostx_main_20260209_120000_1.log"""
    base, ext = default_name.rsplit(".", 1) if "." in default_name else (default_name, "")
    if ext.isdigit():
        # It's a rotation number like ".1", ".2", etc.
        return f"{base}_{ext}.log"
    return default_name

# Create rotating file handler with 5MB cap
_file_handler = logging.handlers.RotatingFileHandler(
    _log_file,
    maxBytes=5 * 1024 * 1024,  # 5MB per file
    backupCount=20,  # Keep 20 rotated files (100MB total max per session)
    encoding="utf-8",
)
_file_handler.namer = _timestamped_namer


# Create root logger configuration
_admin_log_level_name = os.environ.get("API_COST_X_LOG_LEVEL", "INFO").upper()
_admin_log_level = getattr(logging, _admin_log_level_name, logging.INFO)
logging.basicConfig(
    level=_admin_log_level,
    format=_log_format,
    handlers=[
        logging.StreamHandler(sys.stdout),  # Console output (goes to journalctl)
        _file_handler,
    ],
)

logger = logging.getLogger(__name__)

_REQUEST_COUNTER = Counter(
    "apicostx_http_requests_total",
    "Total API service HTTP requests by method, route, and response status.",
    ["method", "route", "status_code", "status_class"],
)
_REQUEST_DURATION = Histogram(
    "apicostx_http_request_duration_seconds",
    "API service request latency by method and route.",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)
_REQUESTS_IN_PROGRESS = Gauge(
    "apicostx_http_requests_in_progress",
    "Current API service requests in progress.",
)
_ACTIVE_RUNS_GAUGE = Gauge(
    "apicostx_active_runs",
    "Current number of APICostX active in-process runs.",
)
_FINALIZATION_SWEEPER_INTERVAL_SECONDS = max(
    15,
    int(os.environ.get("API_COST_X_FINALIZATION_SWEEPER_INTERVAL_SECONDS", "60")),
)


def _resolve_metrics_route_label(request: Request) -> str:
    """Use the route template when possible to avoid high-cardinality labels."""
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if route_path:
        return route_path
    return "_unmatched"


def _status_class(status_code: int) -> str:
    """Bucket HTTP status codes into dashboard-friendly classes like 2xx."""
    return f"{int(status_code) // 100}xx"


def _can_access_metrics(request: Request) -> bool:
    """Allow metrics scraping only from loopback or private-network clients."""
    client = request.client.host if request.client else ""
    try:
        address = ip_address(client)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def _set_active_runs_gauge() -> None:
    """Refresh the active-runs gauge from the in-memory executor registry."""
    try:
        from app.api.routes.runs.execution import _active_executors
        _ACTIVE_RUNS_GAUGE.set(len(_active_executors))
    except Exception:
        logger.exception("Failed to refresh active-runs gauge")


async def _periodic_finalization_recovery_loop() -> None:
    """Continuously repair interrupted finalization for local runs."""
    from app.services.run_finalization_recovery import reconcile_cached_active_runs

    await asyncio.sleep(_FINALIZATION_SWEEPER_INTERVAL_SECONDS)
    while True:
        try:
            summary = await reconcile_cached_active_runs()
            if summary["reconciled"] > 0:
                logger.info(
                    "Periodic finalization sweeper reconciled %d run(s) across %d active candidate run(s); outcomes=%s",
                    summary["reconciled"],
                    summary["active_runs_seen"],
                    summary.get("repair_outcomes", {}),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Periodic finalization sweeper failed")
        await asyncio.sleep(_FINALIZATION_SWEEPER_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Install ContextVar-backed os.environ proxy FIRST — before any adapter
    # or key injection code runs. This enables per-request env overrides for
    # per-run environment isolation. See docs/plan-GPTR-INSTANCE-REFACTOR-FINAL.md
    from app.infra.env_context import install_os_environ_proxy_once
    install_os_environ_proxy_once()

    logger.info("Starting APICostX API server...")

    settings = get_settings()

    db_init = initialize_main_database_from_base(settings)
    if db_init.copied_database:
        logger.info("Initialized main SQLite database from bundled base DB: %s", db_init.database_path)
    else:
        logger.info("Using main SQLite database: %s", db_init.database_path)
    if db_init.copied_artifacts:
        logger.info("Installed %d bundled sample artifact file(s)", db_init.copied_artifacts)

    # Initialize the local single-user registry and database.
    logger.info("Loading local self-hosted user registry...")
    user_count = load_registry()

    logger.info(f"Local user registry loaded: {user_count} user found")


    # ORPHAN RECOVERY: Mark any running runs as failed after restart.
    from app.services.run_finalization_recovery import reconcile_cached_active_runs
    startup_recovery = await reconcile_cached_active_runs(fail_unreconciled=True)

    if startup_recovery["failed"] > 0:
        logger.info(
            "Orphan recovery complete: marked %d orphaned runs as failed",
            startup_recovery["failed"],
        )
    if startup_recovery["reconciled"] > 0:
        logger.info(
            "Orphan recovery complete: reconciled %d interrupted finalization run(s); outcomes=%s",
            startup_recovery["reconciled"],
            startup_recovery.get("repair_outcomes", {}),
        )
    if startup_recovery["user_errors"] > 0:
        logger.warning(
            "Orphan recovery encountered %d user-level error(s)",
            startup_recovery["user_errors"],
        )


    # Check for unexpected FPF log file accumulation
    fpf_logs_dir = Path(__file__).resolve().parent.parent.parent / "packages" / "FilePromptForge" / "logs"
    if fpf_logs_dir.exists():
        fpf_file_count = sum(1 for _ in fpf_logs_dir.rglob("*") if _.is_file())
        if fpf_file_count > 10:
            logger.warning("FPF logs dir has %d files — expected empty after logging redesign", fpf_file_count)

    sweeper_task = asyncio.create_task(_periodic_finalization_recovery_loop())

    # Startup complete
    try:
        yield
    finally:
        sweeper_task.cancel()
        with suppress(asyncio.CancelledError):
            await sweeper_task

        # Shutdown
        await engine.dispose()

        logger.info("Shutting down APICostX API server...")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()
    logger.debug("Using database URL: %s", settings.database_url)

    app = FastAPI(
        title="APICostX Self-Hosted",
        description="Local research evaluation platform",
        version="2.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        redirect_slashes=False,  # Disable 307 redirects - TrailingSlashMiddleware handles normalization
    )

    # CORS middleware for the web GUI. The allowlist lives in app.config.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.resolved_cors_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    # TRAILING SLASH MIDDLEWARE - CRITICAL
    # Normalizes /path/ to /path so both work without 307 redirects.
    # 307 redirects are avoided for simpler local development.
    app.add_middleware(TrailingSlashMiddleware)

    # Docs-only browser hardening. Swagger/ReDoc load third-party assets, so
    # their browser policy is scoped to docs routes only.
    app.add_middleware(DocsSecurityHeadersMiddleware)

    # NO-CACHE MIDDLEWARE - CRITICAL
    # Caching has caused 3 years of "works once, never again" bugs.
    # This middleware ensures NO API response is ever cached.
    app.add_middleware(NoCacheMiddleware)


    @app.middleware("http")
    async def instrument_api_requests(request: Request, call_next):
        if request.url.path == "/metrics":
            return await call_next(request)

        method = request.method
        status_code = 500
        started_at = perf_counter()
        _REQUESTS_IN_PROGRESS.inc()
        try:
            from app.api.routes.internal_dashboard import increment_requests_in_progress
            increment_requests_in_progress()
        except Exception:
            logger.exception("Failed to increment internal dashboard in-progress counter")
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            elapsed = perf_counter() - started_at
            route_label = _resolve_metrics_route_label(request)
            _REQUESTS_IN_PROGRESS.dec()
            try:
                from app.api.routes.internal_dashboard import decrement_requests_in_progress
                decrement_requests_in_progress()
            except Exception:
                logger.exception("Failed to decrement internal dashboard in-progress counter")
            _REQUEST_COUNTER.labels(
                method=method,
                route=route_label,
                status_code=str(status_code),
                status_class=_status_class(status_code),
            ).inc()
            _REQUEST_DURATION.labels(method=method, route=route_label).observe(elapsed)
            try:
                from app.api.routes.internal_dashboard import record_request_summary_event
                record_request_summary_event(
                    route=route_label,
                    status_code=status_code,
                    status_class=_status_class(status_code),
                    elapsed_seconds=elapsed,
                )
            except Exception:
                logger.exception("Failed to record internal dashboard request summary event")

    # Include API routes
    app.include_router(api_router)

    # Validation error handler - log full details for debugging
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        error_locations = [
            ".".join(str(part) for part in err.get("loc", ()))
            for err in exc.errors()[:10]
        ]
        logger.error(
            "Validation error on %s %s: error_count=%d locations=%s",
            request.method,
            request.url.path,
            len(exc.errors()),
            error_locations,
        )
        return JSONResponse(
            status_code=422,
            content={"detail": exc.errors()},
        )

    # Generic exception handler - log full traceback for debugging
    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        tb = "".join(traceback.format_tb(exc.__traceback__)).rstrip()
        logger.error(
            "[UNHANDLED] %s %s -> %s%s%s",
            request.method, request.url.path,
            type(exc).__name__,
            "\n" if tb else "",
            tb,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    # The web GUI is served separately by Vite or the local installer.
    @app.get("/", include_in_schema=False)
    async def root():
        return {"service": "APICostX", "version": "2.0.0", "docs": "/docs", "api": "/api"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request):
        if not _can_access_metrics(request):
            logger.warning("Rejected metrics scrape from %s", request.client.host if request.client else "unknown")
            raise HTTPException(status_code=403, detail="Metrics are only available on the private network")

        _set_active_runs_gauge()
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


# Create app instance
app = create_app()


if __name__ == "__main__":
    import sys
    import uvicorn

    # Windows requires ProactorEventLoop for subprocess support with reload mode
    if sys.platform == 'win32':
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    api_host = os.environ.get("API_COST_X_HOST", "127.0.0.1")
    api_port = int(os.environ.get("API_COST_X_API_PORT", "8000"))
    print(f"Starting API Cost X service on http://{api_host}:{api_port}")
    uvicorn.run("app.main:app", host=api_host, port=api_port, reload=False)
