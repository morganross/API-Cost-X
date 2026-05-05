"""
Database session management.

Provides both shared database access (for backwards compatibility) and
local SQLite database sessions.
"""
from collections import OrderedDict
from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
import time
from typing import AsyncGenerator, Dict, Any, Optional, Set

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import event, text

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)
# DB URL logged at DEBUG only — avoid leaking connection strings at INFO+
logging.getLogger(__name__).debug("SESSION DB URL: %s", settings.database_url)

_USER_ENGINE_IDLE_TTL_SECONDS = 900
_USER_ENGINE_CACHE_MAX_USERS = 64

# Cache for local engines to avoid creating new engine for each request.
# Entries are bounded by idle TTL and LRU size so baseline RAM does not grow forever.
_user_engines: OrderedDict[str, Any] = OrderedDict()
_user_session_factories: Dict[str, async_sessionmaker] = {}
_user_schema_valid: Dict[str, bool] = {}
_user_engine_last_touch: Dict[str, float] = {}


def _set_sqlite_pragma(dbapi_conn, connection_record):
    """Enable WAL mode and other performance settings for SQLite."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    future=True,
)

# Enable WAL mode for the shared database
if "sqlite" in settings.database_url:
    event.listen(engine.sync_engine, "connect", _set_sqlite_pragma)

# Session factory
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get a database session as async context manager."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for FastAPI routes - uses SHARED database.

    DEPRECATED: Use get_user_db_session() for local SQLite access.
    This still works for backwards compatibility but all data is shared.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _db_url_from_path(db_path: Path) -> str:
    """Build a plain aiosqlite URL from an existing DB path."""
    return f"sqlite+aiosqlite:///{db_path}"


def _touch_user_engine_cache(user_uuid: str) -> None:
    if user_uuid in _user_engines:
        _user_engines.move_to_end(user_uuid, last=True)
    _user_engine_last_touch[user_uuid] = time.time()


def _next_evictable_user(exclude: Set[str]) -> Optional[str]:
    for candidate in _user_engines.keys():
        if candidate not in exclude:
            return candidate
    return None


async def _prune_user_engine_cache(*, exclude: Optional[Set[str]] = None) -> None:
    excluded = exclude or set()
    now = time.time()

    expired = [
        user_uuid
        for user_uuid, last_touch in list(_user_engine_last_touch.items())
        if user_uuid not in excluded and (now - last_touch) >= _USER_ENGINE_IDLE_TTL_SECONDS
    ]
    for user_uuid in expired:
        await evict_user_engine(user_uuid)

    while len(_user_engines) > _USER_ENGINE_CACHE_MAX_USERS:
        candidate = _next_evictable_user(excluded)
        if candidate is None:
            break
        await evict_user_engine(candidate)


async def evict_user_engine(user_uuid: str) -> None:
    """Remove cached engine / session-factory for *user_uuid*.

    Called by user_registry.set_membership() before renaming the DB file
    so that the next request rebuilds the engine from the new path.
    Awaited so dispose() completes before the caller renames the file.
    """
    engine_obj = _user_engines.pop(user_uuid, None)
    _user_session_factories.pop(user_uuid, None)
    _user_schema_valid.pop(user_uuid, None)
    _user_engine_last_touch.pop(user_uuid, None)
    if engine_obj is not None:
        await engine_obj.dispose()
    logging.getLogger(__name__).debug(
        "[SESSION] evict_user_engine: evicted uuid=...%s", user_uuid[-8:]
    )


def _build_user_engine(db_url: str, user_uuid: str):
    """Build an async engine for a local plain SQLite database."""
    user_engine = create_async_engine(
        db_url,
        echo=settings.debug,
        future=True,
    )
    event.listen(user_engine.sync_engine, "connect", _set_sqlite_pragma)
    logger.debug(
        "[SESSION] _build_user_engine: built plain SQLite engine for uuid=%s url=%s",
        user_uuid,
        db_url,
    )
    return user_engine


async def _has_required_runs_schema(conn) -> bool:
    result = await conn.execute(text("PRAGMA table_info(runs)"))
    columns = {row[1] for row in result.fetchall()}
    return "preset_id" in columns


async def _run_column_migrations(conn) -> None:
    """
    Apply incremental column additions to existing tables.

    SQLite does not support ADD COLUMN IF NOT EXISTS, so we check PRAGMA
    table_info first and only alter if the column is missing.  This is safe
    to call on every startup — it is a no-op when columns already exist.
    """
    # ── tasks table additions (run-resume feature) ──────────────────────────
    result = await conn.execute(text("PRAGMA table_info(tasks)"))
    task_cols = {row[1] for row in result.fetchall()}

    task_migrations = [
        ("phase",        "VARCHAR(20)  DEFAULT NULL"),
        ("generator",    "VARCHAR(20)  DEFAULT NULL"),
        ("output_ref",   "VARCHAR(500) DEFAULT NULL"),
        ("judge_model",  "VARCHAR(150) DEFAULT NULL"),
        ("retry_count",  "INTEGER      DEFAULT 0"),
    ]
    for col, col_def in task_migrations:
        if col not in task_cols:
            await conn.execute(text(f"ALTER TABLE tasks ADD COLUMN {col} {col_def}"))

    # ── runs table additions (run-resume feature) ────────────────────────────
    result = await conn.execute(text("PRAGMA table_info(runs)"))
    run_cols = {row[1] for row in result.fetchall()}

    run_migrations = [
        ("pause_requested", "INTEGER DEFAULT 0"),
        ("resume_count",    "INTEGER DEFAULT 0"),
    ]
    for col, col_def in run_migrations:
        if col not in run_cols:
            await conn.execute(text(f"ALTER TABLE runs ADD COLUMN {col} {col_def}"))

    # ── presets table rebuild (compiled-truth cutover) ──────────────────────
    result = await conn.execute(text("PRAGMA table_info(presets)"))
    preset_rows = result.fetchall()
    preset_cols = {row[1] for row in preset_rows}

    legacy_preset_cols = {
        "models",
        "generators",
        "evaluation_enabled",
        "pairwise_enabled",
        "log_level",
        "request_timeout",
        "eval_timeout",
        "fpf_max_retries",
        "fpf_retry_delay",
        "eval_retries",
        "generation_concurrency",
        "eval_concurrency",
        "eval_iterations",
        "fpf_log_output",
        "fpf_log_file_path",
        "post_combine_top_n",
        "input_source_type",
        "input_content_ids",
        "github_connection_id",
        "github_input_paths",
        "github_output_path",
        "output_destination",
        "output_filename_template",
        "github_commit_message",
        "prepend_source_first_line_frontmatter",
        "key_mode",
    }
    legacy_present = sorted(preset_cols & legacy_preset_cols)
    if preset_cols and legacy_present:
        logger.warning(
            "[SESSION] Rebuilding presets table to drop legacy columns: columns=%s",
            legacy_present,
        )
        await conn.execute(text("PRAGMA foreign_keys = OFF"))
        try:
            await conn.execute(text("DROP TABLE IF EXISTS presets_rebuilt_new"))
            await conn.execute(
                text(
                    """
                    CREATE TABLE presets_rebuilt_new (
                        id VARCHAR(36) NOT NULL,
                        user_uuid VARCHAR(36),
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME,
                        name VARCHAR(255) NOT NULL,
                        description TEXT,
                        documents JSON NOT NULL,
                        config_overrides JSON,
                        generation_instructions_id VARCHAR(36),
                        single_eval_instructions_id VARCHAR(36),
                        pairwise_eval_instructions_id VARCHAR(36),
                        eval_criteria_id VARCHAR(36),
                        combine_instructions_id VARCHAR(36),
                        CONSTRAINT pk_presets PRIMARY KEY (id),
                        CONSTRAINT fk_presets_generation_instructions_id_contents
                            FOREIGN KEY(generation_instructions_id) REFERENCES contents (id),
                        CONSTRAINT fk_presets_single_eval_instructions_id_contents
                            FOREIGN KEY(single_eval_instructions_id) REFERENCES contents (id),
                        CONSTRAINT fk_presets_pairwise_eval_instructions_id_contents
                            FOREIGN KEY(pairwise_eval_instructions_id) REFERENCES contents (id),
                        CONSTRAINT fk_presets_eval_criteria_id_contents
                            FOREIGN KEY(eval_criteria_id) REFERENCES contents (id),
                        CONSTRAINT fk_presets_combine_instructions_id_contents
                            FOREIGN KEY(combine_instructions_id) REFERENCES contents (id)
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO presets_rebuilt_new (
                        id,
                        user_uuid,
                        created_at,
                        updated_at,
                        name,
                        description,
                        documents,
                        config_overrides,
                        generation_instructions_id,
                        single_eval_instructions_id,
                        pairwise_eval_instructions_id,
                        eval_criteria_id,
                        combine_instructions_id
                    )
                    SELECT
                        id,
                        user_uuid,
                        created_at,
                        updated_at,
                        name,
                        description,
                        documents,
                        config_overrides,
                        generation_instructions_id,
                        single_eval_instructions_id,
                        pairwise_eval_instructions_id,
                        eval_criteria_id,
                        combine_instructions_id
                    FROM presets
                    """
                )
            )
            await conn.execute(text("DROP TABLE presets"))
            await conn.execute(text("ALTER TABLE presets_rebuilt_new RENAME TO presets"))
            await conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_presets_user_uuid ON presets(user_uuid)")
            )
            logger.info("[SESSION] Presets table rebuild complete")
        finally:
            await conn.execute(text("PRAGMA foreign_keys = ON"))

    await _scrub_preset_config_overrides(conn)


async def _scrub_preset_config_overrides(conn) -> None:
    """Normalize persisted preset config_overrides blobs in place."""
    from app.services.config_builder import normalize_config_overrides

    result = await conn.execute(
        text(
            """
            SELECT id, config_overrides
            FROM presets
            WHERE config_overrides IS NOT NULL
            """
        )
    )
    rows = result.fetchall()
    updated = 0
    skipped = 0

    for preset_id, raw_overrides in rows:
        parsed = raw_overrides
        if isinstance(raw_overrides, str):
            try:
                parsed = json.loads(raw_overrides)
            except Exception as exc:
                skipped += 1
                logger.warning(
                    "[SESSION] Skipping config_overrides scrub for preset=%s; JSON parse failed: %s",
                    preset_id,
                    exc,
                )
                continue

        if not isinstance(parsed, dict):
            skipped += 1
            logger.warning(
                "[SESSION] Skipping config_overrides scrub for preset=%s; expected dict, got %s",
                preset_id,
                type(parsed).__name__,
            )
            continue

        normalized = normalize_config_overrides(parsed)
        if normalized == parsed:
            continue

        await conn.execute(
            text(
                """
                UPDATE presets
                SET config_overrides = :config_overrides,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :preset_id
                """
            ),
            {
                "preset_id": preset_id,
                "config_overrides": json.dumps(normalized),
            },
        )
        updated += 1

    logger.info(
        "[SESSION] Preset config_overrides scrub complete: scanned=%s updated=%s skipped=%s",
        len(rows),
        updated,
        skipped,
    )


async def _rebuild_user_db(db_path: Path) -> None:
    """Delete a corrupt user DB (and companions) so it can be recreated."""
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{db_path}{suffix}")
        if path.exists():
            path.unlink()


async def _get_or_create_user_engine(user_uuid: str):
    """Get or create SQLAlchemy engine for a user's database by UUID."""
    from app.auth.user_registry import get_user_db_path

    await _prune_user_engine_cache(exclude={user_uuid})
    db_path = get_user_db_path(user_uuid)
    if db_path is None:
        raise RuntimeError(f"User {user_uuid!r} not in registry — cannot open DB")
    db_url = _db_url_from_path(db_path)

    if user_uuid not in _user_engines:
        user_engine = _build_user_engine(db_url, user_uuid)

        _user_engines[user_uuid] = user_engine
        _user_session_factories[user_uuid] = async_sessionmaker(
            user_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )

            # Initialize tables for this user's database
        from app.infra.db.base import Base
        from app.infra.db.models import preset, run, document, artifact, content, github_connection, user_meta, user_settings  # noqa: F401

        async with user_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await _run_column_migrations(conn)

        # Bootstrap normalized result tables (idempotent — safe on every open)
        from app.infra.db.schema_bootstrap import bootstrap_user_schema
        async with _user_session_factories[user_uuid]() as _bs_session:
            await bootstrap_user_schema(_bs_session)

    if not _user_schema_valid.get(user_uuid):
        user_engine = _user_engines[user_uuid]
        async with user_engine.begin() as conn:
            try:
                is_valid = await _has_required_runs_schema(conn)
            except Exception as schema_exc:
                # Transient error (locked DB, I/O issue) — do not wipe; assume valid
                logging.getLogger(__name__).warning(
                    "[SESSION] Schema check failed for uuid=...%s (transient error): %s — assuming valid",
                    user_uuid[-8:], schema_exc,
                )
                is_valid = True

        if not is_valid:
            logging.getLogger(__name__).error(
                "[SESSION] Per-user database schema invalid for uuid=...%s — "
                "database unavailable until manually repaired. Will NOT auto-wipe.",
                user_uuid[-8:],
            )
            raise RuntimeError(
                f"Per-user database schema is invalid for user {user_uuid!r}. "
                "Database unavailable — contact support."
            )

        _user_schema_valid[user_uuid] = True

    _touch_user_engine_cache(user_uuid)
    await _prune_user_engine_cache(exclude={user_uuid})
    return _user_engines[user_uuid], _user_session_factories[user_uuid]


@asynccontextmanager
async def get_user_session_by_uuid(user_uuid: str) -> AsyncGenerator[AsyncSession, None]:
    """
    Get database session for a user by UUID (for background tasks).

    Use this in background tasks where you don't have a Request object.

    Example:
        async with get_user_session_by_uuid(user_uuid) as session:
            repo = RunRepository(session, user_uuid=user_uuid)
            ...
    """
    _, session_factory = await _get_or_create_user_engine(user_uuid)

    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_user_db_session(user: Dict[str, Any]) -> AsyncGenerator[AsyncSession, None]:
    """
    Get database session for local user (internal use).

    This creates/uses the local self-hosted SQLite database.
    All tables (presets, runs, documents, etc.) are created automatically.
    """
    user_uuid = user['uuid']
    _, session_factory = await _get_or_create_user_engine(user_uuid)

    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ==============================================================================
# PER-USER DATABASE DEPENDENCY FOR ROUTES
# ==============================================================================
#
# SINGLE-USER DATABASE
# ====================
# Self-hosted mode uses one local plain SQLite database identity.
# Route dependencies keep the existing user-shaped plumbing for compatibility.
#
# Usage pattern in route handlers:
#   from app.infra.db.session import get_user_db
#   from app.auth.middleware import get_current_user
#
#   @router.get("/presets")
#   async def list_presets(
#       user: dict = Depends(get_current_user),
#       db: AsyncSession = Depends(get_user_db),
#   ):
#       repo = PresetRepository(db, user_uuid=user['uuid'])
#       ...
#
# NOTE: get_user_db extracts user_uuid from request.state.user which is set
# by get_current_user. Make sure get_current_user is called BEFORE get_user_db
# by listing it first in the function signature.
# ==============================================================================

from fastapi import Request
from fastapi import HTTPException, status


async def get_user_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency: Get local database session.

    This reads user_uuid from request.state.user which MUST be set by
    get_current_user before this dependency runs.

    Routes should declare dependencies in this order:
        user: dict = Depends(get_current_user),  # First - sets request.state.user
        db: AsyncSession = Depends(get_user_db),  # Second - reads user_uuid
    """
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    async for session in get_user_db_session(user):
        yield session


async def init_db() -> None:
    """Initialize database - create all tables."""
    from app.infra.db.base import Base
    # Import all models to register them
    from app.infra.db.models import preset, run, document, artifact, user_meta, user_settings  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Close database connections."""
    for user_uuid in list(_user_engines.keys()):
        await evict_user_engine(user_uuid)
    await engine.dispose()
