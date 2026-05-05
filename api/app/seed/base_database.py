"Initialize the local main SQLite database from the bundled base DB."
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

from app.config import Settings, get_settings

BASE_DATABASE_FILENAME = "api-cost-x.seed.db"


@dataclass(frozen=True)
class BaseDatabaseInitResult:
    """Result of attempting to install the bundled base database/assets."""

    database_path: Path
    base_database_path: Path
    copied_database: bool
    copied_artifacts: int


def bundled_base_database_path() -> Path:
    """Return the tracked, sanitized base database path."""
    return Path(__file__).resolve().parent / BASE_DATABASE_FILENAME


def bundled_artifacts_path() -> Path:
    """Return the tracked sample artifact root copied beside the runtime DB."""
    return Path(__file__).resolve().parent / "artifacts"


def sqlite_database_path(database_url: str) -> Path:
    """Extract a filesystem path from a SQLite SQLAlchemy URL."""
    prefixes = ("sqlite+aiosqlite:///", "sqlite:///")
    for prefix in prefixes:
        if database_url.startswith(prefix):
            return Path(database_url[len(prefix):])
    raise ValueError("APICostX self-hosted mode requires a SQLite database URL")


def main_database_path(settings: Settings | None = None) -> Path:
    """Return the configured runtime main SQLite database path."""
    settings = settings or get_settings()
    return sqlite_database_path(settings.database_url)


def _copy_seed_artifacts(settings: Settings) -> int:
    source_root = bundled_artifacts_path()
    if not source_root.exists():
        return 0

    copied = 0
    for source in source_root.rglob("*"):
        if not source.is_file():
            continue
        relative_path = source.relative_to(source_root)
        target = settings.data_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            continue
        shutil.copy2(source, target)
        copied += 1
    return copied


def initialize_main_database_from_base(settings: Settings | None = None) -> BaseDatabaseInitResult:
    """Copy the bundled base DB to the main DB on first install/start only.

    Existing runtime databases are never overwritten. This keeps presets and
    sample history in the same SQLite file the app mutates at runtime.
    """
    settings = settings or get_settings()
    settings.ensure_dirs()

    source = bundled_base_database_path()
    target = main_database_path(settings)
    target.parent.mkdir(parents=True, exist_ok=True)

    copied_database = False
    if (not target.exists()) or target.stat().st_size == 0:
        if source.exists():
            shutil.copy2(source, target)
            try:
                target.chmod(0o600)
            except OSError:
                pass
            copied_database = True

    copied_artifacts = _copy_seed_artifacts(settings)
    return BaseDatabaseInitResult(
        database_path=target,
        base_database_path=source,
        copied_database=copied_database,
        copied_artifacts=copied_artifacts,
    )
