"""
Per-user path resolution utilities.

All file access for runs must use these helpers to ensure proper isolation.
User identification is by UUID string (not integer).
"""
from pathlib import Path

from app.config import get_settings


def get_user_data_root(user_uuid: str) -> Path:
    """Get the root data directory for a user by UUID."""
    settings = get_settings()
    return settings.data_dir / f"user_{user_uuid}"


def get_user_log_db_path(user_uuid: str) -> Path:
    """Get path to a user's sidecar log database."""
    return get_user_data_root(user_uuid) / "logs.db"


def get_user_run_path(user_uuid: str, run_id: str, subdir: str = "") -> Path:
    """
    Get canonical path for local run files.

    Args:
        user_uuid: The user's UUID
        run_id: The run's UUID
        subdir: Optional subdirectory (generated, logs, reports)

    Returns:
        Path to user's run directory or subdirectory
    """
    base = get_user_data_root(user_uuid) / "runs" / run_id
    if subdir:
        return base / subdir
    return base


def get_generated_doc_path(user_uuid: str, run_id: str, doc_id: str) -> Path:
    """Get path to a generated document file."""
    safe_doc_id = doc_id.replace(':', '_').replace('/', '_').replace('\\', '_')
    return get_user_run_path(user_uuid, run_id, "generated") / f"{safe_doc_id}.md"


def get_report_path(user_uuid: str, run_id: str, filename: str) -> Path:
    """Get path to a report file."""
    return get_user_run_path(user_uuid, run_id, "reports") / filename


def get_log_path(user_uuid: str, run_id: str, filename: str) -> Path:
    """Get path to a log file."""
    return get_user_run_path(user_uuid, run_id, "logs") / filename


def validate_path_ownership(path: Path, user_uuid: str) -> bool:
    """
    Validate that a path is within the user's data directory.

    Use this to prevent path traversal attacks.
    """
    settings = get_settings()
    user_root = settings.data_dir / f"user_{user_uuid}"
    try:
        return path.resolve().is_relative_to(user_root.resolve())
    except ValueError:
        return False
