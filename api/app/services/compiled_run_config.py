"""
Compiled run-config helpers.

This module is the first step toward frozen compiled execution truth.
It serializes a validated ``RunConfig`` into a JSON-safe artifact and
hydrates it back into a ``RunConfig`` without consulting mixed legacy state.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, UTC
from typing import Any, Optional

from app.adapters.base import GeneratorType as AdapterGeneratorType
from app.services.run_executor import RunConfig

COMPILED_RUN_CONFIG_VERSION = 1

logger = logging.getLogger(__name__)

_RUNTIME_ONLY_FIELDS = {
    "on_progress",
    "on_gen_complete",
    "on_eval_complete",
    "on_gen_cached",
    "completed_generation_cache",
    "completed_eval_cache",
    "get_all_eval_scores",
}


def _serialize_generators(generators: list[Any]) -> list[str]:
    serialized: list[str] = []
    for generator in generators or []:
        value = getattr(generator, "value", generator)
        if isinstance(value, str) and value:
            serialized.append(value)
    return serialized


def _canonicalize_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonicalize_payload(payload).encode("utf-8")).hexdigest()


def serialize_compiled_run_config(config: RunConfig) -> dict[str, Any]:
    """
    Serialize a validated ``RunConfig`` into a stable JSON-safe artifact.
    """
    payload: dict[str, Any] = {}
    for field_name in config.__dataclass_fields__:
        if field_name in _RUNTIME_ONLY_FIELDS:
            continue
        value = getattr(config, field_name)
        if field_name == "generators":
            payload[field_name] = _serialize_generators(value)
        else:
            payload[field_name] = value

    return {
        "version": COMPILED_RUN_CONFIG_VERSION,
        "built_at": datetime.now(UTC).isoformat(),
        "hash": _hash_payload(payload),
        "config": payload,
    }


def validate_compiled_run_config_artifact(compiled: Any) -> dict[str, Any]:
    """
    Validate the wrapper artifact around a compiled run-config payload.
    """
    if not isinstance(compiled, dict):
        raise ValueError("compiled_config must be an object")

    version = compiled.get("version")
    if version != COMPILED_RUN_CONFIG_VERSION:
        raise ValueError(
            f"compiled_config version {version!r} is not supported; expected {COMPILED_RUN_CONFIG_VERSION}"
        )

    payload = compiled.get("config")
    if not isinstance(payload, dict):
        raise ValueError("compiled_config.config must be an object")

    required_keys = {
        "document_ids",
        "document_contents",
        "generators",
        "models",
        "model_settings",
        "instructions",
    }
    missing = sorted(key for key in required_keys if key not in payload)
    if missing:
        raise ValueError(f"compiled_config.config missing required keys: {', '.join(missing)}")

    expected_hash = _hash_payload(payload)
    actual_hash = compiled.get("hash")
    if not isinstance(actual_hash, str) or actual_hash != expected_hash:
        raise ValueError("compiled_config hash mismatch")

    return payload


def extract_compiled_run_config_payload(config: Any) -> Optional[dict[str, Any]]:
    """
    Return the compiled run-config payload when present.
    """
    if not isinstance(config, dict):
        return None
    compiled = config.get("compiled_config")
    if compiled is None:
        return None
    try:
        return validate_compiled_run_config_artifact(compiled)
    except Exception as exc:
        version = compiled.get("version") if isinstance(compiled, dict) else None
        logger.warning(
            "[COMPILED CONFIG] Rejecting compiled artifact during extraction: version=%r reason=%s",
            version,
            exc,
        )
        return None


def hydrate_compiled_run_config(compiled_payload: dict[str, Any]) -> RunConfig:
    """
    Hydrate a ``RunConfig`` from a compiled artifact payload.
    """
    payload = dict(compiled_payload or {})
    payload["generators"] = [
        AdapterGeneratorType(generator)
        for generator in payload.get("generators") or []
    ]
    return RunConfig(**payload)
