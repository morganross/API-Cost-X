#!/usr/bin/env python3
"""
Read-only preset compile audit for compiled-truth certification.

This tool opens one user DB with a supplied DB key, enumerates active presets,
and records whether each preset can build a truthful run snapshot and compiled
execution artifact.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.auth.user_registry import load_registry, user_exists
from app.services.config_builder import derive_canonical_preset_config_state
from app.infra.db.models.preset import Preset
from app.infra.db.sqlcipher_dialect import register_dialect
from app.infra.db.session import get_user_session_by_uuid
from app.security.db_crypto import cache_db_key
from app.services.preset_execution import (
    PresetLaunchValidationError,
    build_compiled_run_config_from_run_snapshot,
    build_executor_config_from_run_snapshot,
    build_run_snapshot_from_preset,
    get_preset_runtime_readiness,
    get_static_preset_validation_errors,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit active presets for truthful compile readiness.",
    )
    parser.add_argument("--user-uuid", required=True, help="User UUID to audit")
    parser.add_argument(
        "--db-key-hex",
        required=True,
        help="32-byte user DB key as 64 hex chars",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write the JSON audit report",
    )
    return parser.parse_args()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _coerce_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_coerce_json(item) for item in value]
    return value


def _find_dropped_key_paths(raw: Any, normalized: Any, prefix: str = "") -> list[str]:
    raw_dict = raw if isinstance(raw, dict) else {}
    normalized_dict = normalized if isinstance(normalized, dict) else {}
    dropped: list[str] = []

    for key in sorted(raw_dict.keys()):
        key_str = str(key)
        path = f"{prefix}.{key_str}" if prefix else key_str
        if key not in normalized_dict:
            dropped.append(path)
            continue
        raw_child = raw_dict.get(key)
        normalized_child = normalized_dict.get(key)
        if isinstance(raw_child, dict) and isinstance(normalized_child, dict):
            dropped.extend(_find_dropped_key_paths(raw_child, normalized_child, path))

    return dropped


def _summarize_compiled_config(compiled_config: dict[str, Any]) -> dict[str, Any]:
    compiled_payload = compiled_config.get("config") if isinstance(compiled_config, dict) else {}
    if not isinstance(compiled_payload, dict):
        compiled_payload = {}
    return {
        "version": compiled_config.get("version"),
        "hash": compiled_config.get("hash"),
        "built_at": compiled_config.get("built_at"),
        "required_keys_present": {
            key: key in compiled_payload
            for key in (
                "document_ids",
                "document_contents",
                "generators",
                "models",
                "model_settings",
                "instructions",
            )
        },
        "section_presence": {
            "eval_instructions": bool(compiled_payload.get("single_eval_instructions")),
            "pairwise_instructions": bool(compiled_payload.get("pairwise_eval_instructions")),
            "eval_criteria": bool(compiled_payload.get("eval_criteria")),
            "combine_instructions": bool(compiled_payload.get("combine_instructions")),
            "document_relative_paths": bool(compiled_payload.get("document_relative_paths")),
        },
        "document_count": len(compiled_payload.get("document_ids") or []),
        "generators": compiled_payload.get("generators"),
        "models": compiled_payload.get("models"),
        "request_timeout": compiled_payload.get("request_timeout"),
        "eval_timeout": compiled_payload.get("eval_timeout"),
        "eval_retries": compiled_payload.get("eval_retries"),
        "fpf_max_retries": compiled_payload.get("fpf_max_retries"),
        "fpf_retry_delay": compiled_payload.get("fpf_retry_delay"),
        "generation_concurrency": compiled_payload.get("generation_concurrency"),
        "eval_concurrency": compiled_payload.get("eval_concurrency"),
        "enable_single_eval": compiled_payload.get("enable_single_eval"),
        "enable_pairwise": compiled_payload.get("enable_pairwise"),
        "enable_combine": compiled_payload.get("enable_combine"),
        "key_mode": compiled_payload.get("key_mode"),
    }


async def _audit_preset(preset: Preset, *, user_uuid: str, db) -> dict[str, Any]:
    raw_overrides = _coerce_json(preset.config_overrides or {})
    canonical_state = derive_canonical_preset_config_state(
        config_overrides=preset.config_overrides or {},
    )
    normalized_overrides = _coerce_json(canonical_state.get("overrides") or {})
    user = {"uuid": user_uuid}
    result: dict[str, Any] = {
        "preset_id": preset.id,
        "name": preset.name,
        "description": preset.description,
        "created_at": _coerce_json(preset.created_at),
        "updated_at": _coerce_json(preset.updated_at),
        "document_ids": list(preset.documents or []),
        "config_overrides": raw_overrides,
        "normalized_config_overrides": normalized_overrides,
        "dead_key_findings": _find_dropped_key_paths(raw_overrides, normalized_overrides),
        "canonical_sections_present": {
            "general": bool(canonical_state.get("general_config")),
            "concurrency": bool(canonical_state.get("concurrency_config")),
            "fpf": bool(canonical_state.get("fpf_config")),
            "gptr": bool(canonical_state.get("gptr_config")),
            "dr": bool(canonical_state.get("dr_config")),
            "ma": bool(canonical_state.get("ma_config")),
            "aiq": bool(canonical_state.get("aiq_config")),
            "eval": bool(canonical_state.get("eval_config")),
            "pairwise": bool(canonical_state.get("pairwise_config")),
            "combine": bool(canonical_state.get("combine_config")),
            "launch": bool(canonical_state.get("launch_config")),
        },
        "derived_launch_state": {
            "generators": list(canonical_state.get("generators") or []),
            "models": _coerce_json(canonical_state.get("models") or []),
            "evaluation_enabled": bool(canonical_state.get("evaluation_enabled")),
            "pairwise_enabled": bool(canonical_state.get("pairwise_enabled")),
            "save_run_logs": bool(canonical_state.get("save_run_logs")),
        },
        "static_validation_errors": list(get_static_preset_validation_errors(preset)),
    }

    readiness = await get_preset_runtime_readiness(
        preset,
        user=user,
        db=db,
    )
    result["runtime_readiness"] = {
        "runnable": readiness.runnable,
        "errors": list(readiness.errors),
    }

    snapshot: dict[str, Any] | None = None
    try:
        snapshot = await build_run_snapshot_from_preset(
            preset,
            user=user,
            db=db,
        )
        result["run_snapshot"] = {
            "document_count": len(snapshot.get("document_ids") or []),
            "generators": list(snapshot.get("generators") or []),
            "iterations": snapshot.get("iterations"),
            "evaluation_enabled": snapshot.get("evaluation_enabled"),
            "pairwise_enabled": snapshot.get("pairwise_enabled"),
            "save_run_logs": snapshot.get("save_run_logs"),
        }
    except PresetLaunchValidationError as exc:
        result["run_snapshot_error"] = list(exc.errors)
        return result

    try:
        compiled = await build_compiled_run_config_from_run_snapshot(
            run_id=f"preset-audit-{preset.id[:8]}",
            run_config=snapshot,
            preset=preset,
            user=user,
            db=db,
        )
        result["compiled_config"] = _summarize_compiled_config(compiled)
    except PresetLaunchValidationError as exc:
        result["compiled_config_error"] = list(exc.errors)
        return result

    try:
        executor_config, _document_contents = await build_executor_config_from_run_snapshot(
            run_id=f"preset-audit-{preset.id[:8]}",
            run_config=snapshot,
            preset=preset,
            user=user,
            db=db,
        )
        result["executor_config"] = {
            "request_timeout": executor_config.request_timeout,
            "eval_timeout": executor_config.eval_timeout,
            "eval_retries": executor_config.eval_retries,
            "fpf_max_retries": executor_config.fpf_max_retries,
            "fpf_retry_delay": executor_config.fpf_retry_delay,
            "generation_concurrency": executor_config.generation_concurrency,
            "eval_concurrency": executor_config.eval_concurrency,
            "key_mode": executor_config.key_mode,
            "generators": [generator.value for generator in executor_config.generators],
            "models": list(executor_config.models),
            "document_names": _coerce_json(executor_config.document_names),
        }
    except PresetLaunchValidationError as exc:
        result["executor_config_error"] = list(exc.errors)
        return result

    result["compile_status"] = "compiled"
    return result


async def _run() -> int:
    args = _parse_args()
    user_uuid = args.user_uuid.strip().lower()
    db_key_hex = args.db_key_hex.strip().lower()
    output_path = Path(args.output).expanduser().resolve()

    if len(db_key_hex) != 64:
        raise SystemExit("--db-key-hex must be 64 hex chars")
    try:
        db_key = bytes.fromhex(db_key_hex)
    except ValueError as exc:
        raise SystemExit(f"--db-key-hex is not valid hex: {exc}") from exc

    register_dialect()
    load_registry()
    if not user_exists(user_uuid):
        raise SystemExit(
            f"User {user_uuid!r} is not present in the on-disk registry scan; "
            "cannot audit a missing user DB."
        )

    cache_db_key(user_uuid, db_key)

    async with get_user_session_by_uuid(user_uuid) as db:
        presets = list(
            (
                await db.execute(
                    select(Preset).where(Preset.user_uuid == user_uuid).order_by(Preset.created_at.asc())
                )
            ).scalars().all()
        )

        report: dict[str, Any] = {
            "generated_at": _utc_now(),
            "user_uuid": user_uuid,
            "preset_count": len(presets),
            "compiled_count": 0,
            "failed_count": 0,
            "presets": [],
        }

        for preset in presets:
            preset_result = await _audit_preset(preset, user_uuid=user_uuid, db=db)
            report["presets"].append(preset_result)
            if preset_result.get("compile_status") == "compiled":
                report["compiled_count"] += 1
            else:
                report["failed_count"] += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"output": str(output_path), "preset_count": report["preset_count"], "compiled_count": report["compiled_count"], "failed_count": report["failed_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
