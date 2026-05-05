"""
In-process cache for safe terminal run-detail payloads.

This cache is intentionally API-service-local and RAM-only.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import time
from collections import OrderedDict
from typing import Optional

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = int(os.environ.get("API_COST_X_RUN_DETAIL_CACHE_TTL_SECONDS", "600"))
_CACHE_MAX_ENTRIES = int(os.environ.get("API_COST_X_RUN_DETAIL_CACHE_MAX_ENTRIES", "64"))
_CACHE_MAX_PAYLOAD_BYTES = int(
    os.environ.get("API_COST_X_RUN_DETAIL_CACHE_MAX_PAYLOAD_BYTES", str(1024 * 1024))
)
_cache: "OrderedDict[tuple[str, str, str, Optional[str]], tuple[float, dict]]" = OrderedDict()


def _normalize_include(include: str) -> str:
    return ",".join(sorted({part.strip() for part in include.split(",") if part.strip()}))


def _make_key(
    user_uuid: str,
    run_id: str,
    include: str,
    source_doc_id: Optional[str],
) -> tuple[str, str, str, Optional[str]]:
    return (user_uuid, run_id, _normalize_include(include), source_doc_id)


def _estimate_payload_bytes(payload: dict) -> int:
    try:
        serialized = json.dumps(payload, separators=(",", ":"), default=str)
    except Exception:
        return _CACHE_MAX_PAYLOAD_BYTES + 1
    return len(serialized.encode("utf-8"))


def _purge_expired(now: Optional[float] = None) -> None:
    current = time.monotonic() if now is None else now
    expired_keys = [
        key for key, (expires_at, _) in _cache.items()
        if expires_at <= current
    ]
    for key in expired_keys:
        _cache.pop(key, None)


def get_cached_run_detail(
    *,
    user_uuid: str,
    run_id: str,
    include: str,
    source_doc_id: Optional[str],
) -> Optional[dict]:
    now = time.monotonic()
    _purge_expired(now)
    key = _make_key(user_uuid, run_id, include, source_doc_id)
    payload = _cache.get(key)
    if payload is None:
        return None
    expires_at, data = payload
    if expires_at <= now:
        _cache.pop(key, None)
        return None
    _cache.move_to_end(key)
    return copy.deepcopy(data)


def cache_run_detail(
    *,
    user_uuid: str,
    run_id: str,
    include: str,
    source_doc_id: Optional[str],
    payload: dict,
) -> None:
    now = time.monotonic()
    _purge_expired(now)
    key = _make_key(user_uuid, run_id, include, source_doc_id)
    estimated_bytes = _estimate_payload_bytes(payload)
    if estimated_bytes > _CACHE_MAX_PAYLOAD_BYTES:
        _cache.pop(key, None)
        logger.warning(
            "[RUN DETAIL CACHE] Skipping cache for run %s include=%s estimated_bytes=%d limit=%d",
            run_id[:8],
            _normalize_include(include),
            estimated_bytes,
            _CACHE_MAX_PAYLOAD_BYTES,
        )
        return
    _cache[key] = (now + _CACHE_TTL_SECONDS, copy.deepcopy(payload))
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX_ENTRIES:
        _cache.popitem(last=False)


def evict_run_detail(
    *,
    user_uuid: str,
    run_id: str,
) -> None:
    matching_keys = [
        key for key in list(_cache.keys())
        if key[0] == user_uuid and key[1] == run_id
    ]
    for key in matching_keys:
        _cache.pop(key, None)


def evict_user_run_detail_cache(user_uuid: str) -> None:
    matching_keys = [key for key in list(_cache.keys()) if key[0] == user_uuid]
    for key in matching_keys:
        _cache.pop(key, None)
