#!/usr/bin/env python3
"""
Read-only compiled-run readiness and resume audit for compiled-truth certification.

This tool opens one user DB with a supplied DB key, enumerates persisted runs,
and records whether each run has a valid frozen compiled artifact, whether that
artifact can rebuild an executable RunConfig without consulting preset state,
and what the current resumability decision is for that row.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.auth.user_registry import load_registry, user_exists
from app.infra.db.models.preset import Preset
from app.infra.db.models.run import Run
from app.infra.db.sqlcipher_dialect import register_dialect
from app.infra.db.session import get_user_session_by_uuid
from app.security.db_crypto import cache_db_key
from app.services.compiled_run_config import (
    _hash_payload,
    hydrate_compiled_run_config,
    validate_compiled_run_config_artifact,
)
from app.services.preset_execution import (
    PresetLaunchValidationError,
    build_compiled_run_config_from_run_snapshot,
    build_executor_config_from_run_snapshot,
)
from app.services.run_resumability import build_run_resume_info


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit persisted runs for compiled-config readiness and resume honesty.",
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


def _coerce_str_list(raw: Any) -> list[str]:
    values: list[str] = []
    for item in raw or []:
        if isinstance(item, str):
            values.append(item)
        elif isinstance(item, dict):
            provider = str(item.get("provider", "") or "").strip()
            model = str(item.get("model", "") or "").strip()
            values.append(f"{provider}:{model}" if provider and model else json.dumps(item, sort_keys=True))
        else:
            values.append(str(item))
    return values


def _compiled_payload_summary(compiled_artifact: dict[str, Any], compiled_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": compiled_artifact.get("version"),
        "built_at": compiled_artifact.get("built_at"),
        "hash": compiled_artifact.get("hash"),
        "hash_matches_payload": bool(compiled_artifact.get("hash") == _hash_payload(compiled_payload)),
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
        "document_count": len(compiled_payload.get("document_ids") or []),
        "generators": list(compiled_payload.get("generators") or []),
        "models": _coerce_str_list(compiled_payload.get("models") or []),
        "iterations": compiled_payload.get("iterations"),
        "eval_iterations": compiled_payload.get("eval_iterations"),
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


def _hybrid_snapshot_drift(raw_config: dict[str, Any], compiled_payload: dict[str, Any]) -> dict[str, Any]:
    drift: dict[str, Any] = {}

    raw_document_ids = list(raw_config.get("document_ids") or [])
    compiled_document_ids = list(compiled_payload.get("document_ids") or [])
    if raw_document_ids != compiled_document_ids:
        drift["document_ids"] = {
            "run_config": raw_document_ids,
            "compiled_config": compiled_document_ids,
        }

    raw_generators = list(raw_config.get("generators") or [])
    compiled_generators = list(compiled_payload.get("generators") or [])
    if raw_generators != compiled_generators:
        drift["generators"] = {
            "run_config": raw_generators,
            "compiled_config": compiled_generators,
        }

    raw_models = _coerce_str_list(raw_config.get("models") or [])
    compiled_models = _coerce_str_list(compiled_payload.get("models") or [])
    if raw_models != compiled_models:
        drift["models"] = {
            "run_config": raw_models,
            "compiled_config": compiled_models,
        }

    raw_iterations = raw_config.get("iterations")
    compiled_iterations = compiled_payload.get("iterations")
    if raw_iterations != compiled_iterations:
        drift["iterations"] = {
            "run_config": raw_iterations,
            "compiled_config": compiled_iterations,
        }

    raw_eval_enabled = raw_config.get("evaluation_enabled")
    compiled_eval_enabled = compiled_payload.get("enable_single_eval")
    if raw_eval_enabled != compiled_eval_enabled:
        drift["evaluation_enabled"] = {
            "run_config": raw_eval_enabled,
            "compiled_config": compiled_eval_enabled,
        }

    raw_pairwise_enabled = raw_config.get("pairwise_enabled")
    compiled_pairwise_enabled = compiled_payload.get("enable_pairwise")
    if raw_pairwise_enabled != compiled_pairwise_enabled:
        drift["pairwise_enabled"] = {
            "run_config": raw_pairwise_enabled,
            "compiled_config": compiled_pairwise_enabled,
        }

    return drift


def _resume_info_summary(info: dict[str, Any]) -> dict[str, Any]:
    return {
        "resumable": bool(info.get("resumable")),
        "resume_mode": info.get("resume_mode"),
        "reason": info.get("reason"),
        "phase_hint": info.get("phase_hint"),
        "stale_running_tasks": info.get("stale_running_tasks"),
        "reusable_generation_tasks": info.get("reusable_generation_tasks"),
        "reusable_eval_tasks": info.get("reusable_eval_tasks"),
        "reusable_pairwise_tasks": info.get("reusable_pairwise_tasks"),
        "reusable_combine_tasks": info.get("reusable_combine_tasks"),
        "reusable_pre_combine_pairwise_tasks": info.get("reusable_pre_combine_pairwise_tasks"),
        "reusable_post_combine_pairwise_tasks": info.get("reusable_post_combine_pairwise_tasks"),
        "blocking_errors": list(info.get("blocking_errors") or []),
        "warnings": list(info.get("warnings") or []),
        "checkpoint_summary": _coerce_json(info.get("checkpoint_summary") or {}),
    }


async def _audit_run(
    run: Run,
    *,
    preset_by_id: dict[str, Preset],
    preset_name_by_id: dict[str, str],
    user_uuid: str,
    db,
) -> dict[str, Any]:
    raw_config = run.config if isinstance(run.config, dict) else {}
    compiled_artifact = raw_config.get("compiled_config") if isinstance(raw_config, dict) else None

    result: dict[str, Any] = {
        "run_id": run.id,
        "title": run.title,
        "status": run.status,
        "preset_id": run.preset_id,
        "preset_name": preset_name_by_id.get(str(run.preset_id or "")),
        "created_at": _coerce_json(run.created_at),
        "started_at": _coerce_json(run.started_at),
        "completed_at": _coerce_json(run.completed_at),
        "total_tasks": int(run.total_tasks or 0),
        "completed_tasks": int(run.completed_tasks or 0),
        "failed_tasks": int(run.failed_tasks or 0),
        "pause_requested": int(run.pause_requested or 0),
        "resume_count": int(run.resume_count or 0),
        "has_compiled_config": isinstance(compiled_artifact, dict),
        "compiled_config": None,
        "compiled_config_validation_error": None,
        "executor_rebuild": None,
        "stored_snapshot_recompile": None,
        "resume_info": None,
        "hybrid_snapshot_drift": {},
    }

    compiled_payload: dict[str, Any] | None = None
    if isinstance(compiled_artifact, dict):
        try:
            compiled_payload = validate_compiled_run_config_artifact(compiled_artifact)
            result["compiled_config"] = _compiled_payload_summary(compiled_artifact, compiled_payload)
            result["hybrid_snapshot_drift"] = _hybrid_snapshot_drift(raw_config, compiled_payload)
        except Exception as exc:
            result["compiled_config_validation_error"] = str(exc)

    if compiled_payload is not None:
        try:
            hydrated = hydrate_compiled_run_config(compiled_payload)
            result["executor_rebuild"] = {
                "status": "ok",
                "document_count": len(hydrated.document_ids),
                "generators": [generator.value for generator in hydrated.generators],
                "models": list(hydrated.models),
                "request_timeout": hydrated.request_timeout,
                "eval_timeout": hydrated.eval_timeout,
                "eval_retries": hydrated.eval_retries,
                "fpf_max_retries": hydrated.fpf_max_retries,
                "fpf_retry_delay": hydrated.fpf_retry_delay,
                "generation_concurrency": hydrated.generation_concurrency,
                "eval_concurrency": hydrated.eval_concurrency,
                "key_mode": hydrated.key_mode,
            }
        except Exception as exc:
            result["executor_rebuild"] = {
                "status": "error",
                "error": str(exc),
            }

    try:
        executor_config, _document_contents = await build_executor_config_from_run_snapshot(
            run_id=run.id,
            run_config=raw_config,
            preset=None,
            user={"uuid": user_uuid},
            db=db,
        )
        result["executor_rebuild"] = {
            "status": "ok",
            "document_count": len(executor_config.document_ids),
            "generators": [generator.value for generator in executor_config.generators],
            "models": list(executor_config.models),
            "request_timeout": executor_config.request_timeout,
            "eval_timeout": executor_config.eval_timeout,
            "eval_retries": executor_config.eval_retries,
            "fpf_max_retries": executor_config.fpf_max_retries,
            "fpf_retry_delay": executor_config.fpf_retry_delay,
            "generation_concurrency": executor_config.generation_concurrency,
            "eval_concurrency": executor_config.eval_concurrency,
            "key_mode": executor_config.key_mode,
        }
    except PresetLaunchValidationError as exc:
        result["executor_rebuild"] = {
            "status": "error",
            "errors": list(exc.errors),
        }
    except Exception as exc:  # pragma: no cover - defensive capture for audits
        result["executor_rebuild"] = {
            "status": "error",
            "errors": [str(exc)],
        }

    linked_preset = preset_by_id.get(str(run.preset_id or ""))
    if linked_preset is None:
        result["stored_snapshot_recompile"] = {
            "status": "skipped",
            "reason": "Run has no linked preset row available for snapshot recompilation.",
        }
    else:
        try:
            recompiled = await build_compiled_run_config_from_run_snapshot(
                run_id=f"run-audit-{run.id[:8]}",
                run_config=raw_config,
                preset=linked_preset,
                user={"uuid": user_uuid},
                db=db,
            )
            result["stored_snapshot_recompile"] = {
                "status": "ok",
                "hash": recompiled.get("hash"),
                "version": recompiled.get("version"),
                "matches_stored_hash": bool(
                    isinstance(compiled_artifact, dict)
                    and recompiled.get("hash") == compiled_artifact.get("hash")
                ),
            }
        except PresetLaunchValidationError as exc:
            result["stored_snapshot_recompile"] = {
                "status": "error",
                "errors": list(exc.errors),
            }
        except Exception as exc:  # pragma: no cover - defensive capture for audits
            result["stored_snapshot_recompile"] = {
                "status": "error",
                "errors": [str(exc)],
            }

    resume_info = await build_run_resume_info(
        db=db,
        run=run,
        active_executor_present=False,
    )
    result["resume_info"] = _resume_info_summary(resume_info)

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
        preset_rows = list(
            (
                await db.execute(
                    select(Preset).where(Preset.user_uuid == user_uuid)
                )
            ).scalars().all()
        )
        preset_by_id = {str(preset.id): preset for preset in preset_rows}
        preset_name_by_id = {preset_id: preset.name for preset_id, preset in preset_by_id.items()}

        runs = list(
            (
                await db.execute(
                    select(Run).where(Run.user_uuid == user_uuid).order_by(Run.created_at.asc())
                )
            ).scalars().all()
        )

        report: dict[str, Any] = {
            "generated_at": _utc_now(),
            "user_uuid": user_uuid,
            "run_count": len(runs),
            "status_counts": {},
            "compiled_config_counts": {
                "missing": 0,
                "invalid": 0,
                "valid": 0,
            },
            "executor_rebuild_counts": {
                "ok": 0,
                "error": 0,
            },
            "stored_snapshot_recompile_counts": {
                "ok": 0,
                "error": 0,
                "hash_match": 0,
                "hash_mismatch": 0,
            },
            "resume_counts": {
                "resumable": 0,
                "not_resumable": 0,
                "resume_modes": {},
                "reason_counts": {},
            },
            "runs": [],
        }

        status_counts: Counter[str] = Counter()
        resume_mode_counts: Counter[str] = Counter()
        resume_reason_counts: Counter[str] = Counter()

        for run in runs:
            run_result = await _audit_run(
                run,
                preset_by_id=preset_by_id,
                preset_name_by_id=preset_name_by_id,
                user_uuid=user_uuid,
                db=db,
            )
            report["runs"].append(run_result)

            status_counts[str(run.status or "")] += 1

            if not run_result["has_compiled_config"]:
                report["compiled_config_counts"]["missing"] += 1
            elif run_result.get("compiled_config_validation_error"):
                report["compiled_config_counts"]["invalid"] += 1
            else:
                report["compiled_config_counts"]["valid"] += 1

            executor_status = str((run_result.get("executor_rebuild") or {}).get("status") or "error")
            report["executor_rebuild_counts"][executor_status] = (
                report["executor_rebuild_counts"].get(executor_status, 0) + 1
            )

            recompile_status = str((run_result.get("stored_snapshot_recompile") or {}).get("status") or "error")
            report["stored_snapshot_recompile_counts"][recompile_status] = (
                report["stored_snapshot_recompile_counts"].get(recompile_status, 0) + 1
            )
            recompile = run_result.get("stored_snapshot_recompile") or {}
            if recompile.get("status") == "ok":
                if recompile.get("matches_stored_hash"):
                    report["stored_snapshot_recompile_counts"]["hash_match"] += 1
                else:
                    report["stored_snapshot_recompile_counts"]["hash_mismatch"] += 1

            resume_info = run_result.get("resume_info") or {}
            if resume_info.get("resumable"):
                report["resume_counts"]["resumable"] += 1
            else:
                report["resume_counts"]["not_resumable"] += 1
            resume_mode = str(resume_info.get("resume_mode") or "unknown")
            resume_reason = str(resume_info.get("reason") or "unknown")
            resume_mode_counts[resume_mode] += 1
            resume_reason_counts[resume_reason] += 1

        report["status_counts"] = dict(sorted(status_counts.items()))
        report["resume_counts"]["resume_modes"] = dict(sorted(resume_mode_counts.items()))
        report["resume_counts"]["reason_counts"] = dict(
            sorted(resume_reason_counts.items(), key=lambda item: (-item[1], item[0]))
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output_path),
                "run_count": report["run_count"],
                "compiled_config_counts": report["compiled_config_counts"],
                "executor_rebuild_counts": report["executor_rebuild_counts"],
                "resume_counts": {
                    "resumable": report["resume_counts"]["resumable"],
                    "not_resumable": report["resume_counts"]["not_resumable"],
                },
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
