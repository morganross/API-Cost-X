"""No-auth dependencies for the single-user self-hosted app."""
from fastapi import Request
from typing import Optional, Dict, Any
import time
import threading

LOCAL_USER_UUID = "local"
_local_api_users_last_seen: Dict[str, float] = {}
_LOCAL_API_USER_WINDOW_SECONDS = 300
_LOCAL_API_USERS_LOCK = threading.Lock()


def clear_local_user_cache() -> None:
    return None


def _prune_active_api_users(now: float) -> None:
    cutoff = now - _LOCAL_API_USER_WINDOW_SECONDS
    for user_uuid, last_seen in list(_local_api_users_last_seen.items()):
        if last_seen < cutoff:
            del _local_api_users_last_seen[user_uuid]


def record_local_user_activity(user_uuid: str) -> None:
    now = time.time()
    with _LOCAL_API_USERS_LOCK:
        _prune_active_api_users(now)
        _local_api_users_last_seen[user_uuid or LOCAL_USER_UUID] = now


def get_recent_local_user_count(window_seconds: int = _LOCAL_API_USER_WINDOW_SECONDS) -> int:
    now = time.time()
    cutoff = now - max(int(window_seconds), 1)
    with _LOCAL_API_USERS_LOCK:
        _prune_active_api_users(now)
        return sum(1 for last_seen in _local_api_users_last_seen.values() if last_seen >= cutoff)


def _local_user() -> Dict[str, Any]:
    return {"uuid": LOCAL_USER_UUID, "membership": "local"}


async def get_current_user(request: Request) -> Dict[str, Any]:
    """Return the local user. Self-hosted mode has no login or access control."""
    user = _local_user()
    request.state.user = user
    record_local_user_activity(user["uuid"])
    return user


async def get_optional_user(request: Request) -> Optional[Dict[str, Any]]:
    user = _local_user()
    request.state.user = user
    record_local_user_activity(user["uuid"])
    return user


def evict_local_user_cache(user_uuid: str) -> None:
    return None
