"""Single-user local SQLite registry for self-hosted mode."""
from pathlib import Path
from typing import Optional, Set, TypedDict

from app.config import get_settings

LOCAL_USER_UUID = "local"


class UserEntry(TypedDict):
    path: Path
    membership: str


_user_registry: dict[str, UserEntry] = {}


def get_data_dir() -> Path:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings.data_dir


def _local_db_path() -> Path:
    return get_data_dir() / "api-cost-x.db"


def load_registry() -> int:
    _user_registry.clear()
    _user_registry[LOCAL_USER_UUID] = {"path": _local_db_path(), "membership": "local"}
    return 1


def register_user(user_uuid: str = LOCAL_USER_UUID, membership: str = "local") -> Path:
    load_registry()
    return _local_db_path()


def get_user_db_path(user_uuid: str = LOCAL_USER_UUID) -> Optional[Path]:
    if not _user_registry:
        load_registry()
    return _user_registry[LOCAL_USER_UUID]["path"]


def get_membership(user_uuid: str = LOCAL_USER_UUID) -> Optional[str]:
    return "local"


async def set_membership(user_uuid: str, new_status: str) -> Path:
    return register_user()


def user_exists(user_uuid: str = LOCAL_USER_UUID) -> bool:
    return True


def get_user_count() -> int:
    return 1


def get_all_user_uuids() -> Set[str]:
    return {LOCAL_USER_UUID}


def unregister_user(user_uuid: str) -> bool:
    return False
