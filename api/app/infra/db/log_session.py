"""
Sidecar log database session management.

Each user gets a separate logs.db (alongside their main user_<uuid>.db).
This keeps high-volume log writes from contending with the main database.
"""
from collections import OrderedDict
import logging
import time
from typing import Dict, Any, Optional, Set

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import event, text

from app.config import get_settings
from app.infra.db.session import _set_sqlite_pragma

logger = logging.getLogger(__name__)

LOG_DB_FILENAME = "logs.db"
_USER_LOG_ENGINE_IDLE_TTL_SECONDS = 900
_USER_LOG_ENGINE_CACHE_MAX_USERS = 64

# Module-level caches (same pattern as session.py)
_user_log_engines: OrderedDict[str, Any] = OrderedDict()
_user_log_session_factories: Dict[str, async_sessionmaker] = {}
_user_log_engine_last_touch: Dict[str, float] = {}


def _touch_user_log_engine_cache(user_uuid: str) -> None:
    if user_uuid in _user_log_engines:
        _user_log_engines.move_to_end(user_uuid, last=True)
    _user_log_engine_last_touch[user_uuid] = time.time()


def _next_evictable_log_user(exclude: Set[str]) -> Optional[str]:
    for candidate in _user_log_engines.keys():
        if candidate not in exclude:
            return candidate
    return None


async def _prune_user_log_engine_cache(*, exclude: Optional[Set[str]] = None) -> None:
    excluded = exclude or set()
    now = time.time()

    expired = [
        user_uuid
        for user_uuid, last_touch in list(_user_log_engine_last_touch.items())
        if user_uuid not in excluded and (now - last_touch) >= _USER_LOG_ENGINE_IDLE_TTL_SECONDS
    ]
    for user_uuid in expired:
        await evict_user_log_engine(user_uuid)

    while len(_user_log_engines) > _USER_LOG_ENGINE_CACHE_MAX_USERS:
        candidate = _next_evictable_log_user(excluded)
        if candidate is None:
            break
        await evict_user_log_engine(candidate)


async def evict_user_log_engine(user_uuid: str) -> None:
    """Dispose and remove a cached local log engine."""
    engine_obj = _user_log_engines.pop(user_uuid, None)
    _user_log_session_factories.pop(user_uuid, None)
    _user_log_engine_last_touch.pop(user_uuid, None)
    if engine_obj is not None:
        await engine_obj.dispose()
    logger.debug("Evicted log DB engine for user %s", user_uuid[:8])


def _get_user_log_db_url(user_uuid: str) -> str:
    settings = get_settings()
    data_dir = settings.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    user_log_dir = data_dir / f"user_{user_uuid}"
    user_log_dir.mkdir(parents=True, exist_ok=True)
    db_path = user_log_dir / LOG_DB_FILENAME
    return f"sqlite+aiosqlite:///{db_path}"


def _create_log_tables(connection):
    """Create log_entries and schema_version tables (raw SQL, not ORM)."""
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS log_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            classification TEXT NOT NULL CHECK(classification IN ('EVENT', 'DETAIL')),
            source TEXT NOT NULL,
            level TEXT NOT NULL,
            event_type TEXT,
            message TEXT NOT NULL,
            payload TEXT
        )
    """))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_log_run_id ON log_entries(run_id)"
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_log_run_ts ON log_entries(run_id, timestamp)"
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_log_run_class ON log_entries(run_id, classification)"
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_log_run_class_id ON log_entries(run_id, classification, id)"
    ))

    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL,
            applied_at TEXT NOT NULL
        )
    """))
    result = connection.execute(text("SELECT COUNT(*) FROM schema_version")).scalar()
    if result == 0:
        connection.execute(text(
            "INSERT INTO schema_version (version, applied_at) VALUES (1, datetime('now'))"
        ))


async def _get_or_create_user_log_engine(user_uuid: str):
    """Get or create an async engine for a user's logs.db."""
    await _prune_user_log_engine_cache(exclude={user_uuid})
    if user_uuid not in _user_log_engines:
        db_url = _get_user_log_db_url(user_uuid)
        user_log_engine = create_async_engine(db_url, echo=False, future=True)

        event.listen(user_log_engine.sync_engine, "connect", _set_sqlite_pragma)

        _user_log_engines[user_uuid] = user_log_engine
        _user_log_session_factories[user_uuid] = async_sessionmaker(
            user_log_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )

        # Create tables on first access
        async with user_log_engine.begin() as conn:
            await conn.run_sync(_create_log_tables)

        logger.debug("Created log DB engine for user %s", user_uuid[:8])

    _touch_user_log_engine_cache(user_uuid)
    await _prune_user_log_engine_cache(exclude={user_uuid})
    return _user_log_engines[user_uuid]
