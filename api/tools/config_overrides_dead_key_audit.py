#!/usr/bin/env python3
"""
Read-only config_overrides dead-key audit for compiled-truth certification.

This tool opens one user DB with a supplied DB key, enumerates active presets,
and records whether stored config_overrides blobs are already canonical, still
carry unsupported keys, still rely on deprecated alias shapes, or encode known
concepts in the wrong section.
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
from app.infra.db.sqlcipher_dialect import register_dialect
from app.infra.db.session import get_user_session_by_uuid
from app.security.db_crypto import cache_db_key
from app.services.config_builder import derive_canonical_preset_config_state, normalize_config_overrides


_SECTION_ALLOWED_KEYS: dict[str, set[str]] = {
    "general": {
        "iterations",
        "use_byok_first",
        "save_run_logs",
        "post_combine_top_n",
        "run_estimate",
        "expose_criteria_to_generators",
    },
    "concurrency": {
        "generation_concurrency",
        "eval_concurrency",
        "request_timeout",
        "fpf_max_retries",
        "fpf_retry_delay",
    },
    "fpf": {
        "enabled",
        "selected_models",
        "max_tokens",
        "temperature",
        "top_p",
        "top_k",
        "frequency_penalty",
        "presence_penalty",
        "stream_response",
        "include_metadata",
        "save_prompt_history",
    },
    "gptr": {
        "enabled",
        "selected_models",
        "fast_llm_token_limit",
        "smart_llm_token_limit",
        "strategic_llm_token_limit",
        "browse_chunk_max_length",
        "summary_token_limit",
        "temperature",
        "max_search_results_per_query",
        "total_words",
        "max_iterations",
        "max_subtopics",
        "report_type",
        "report_source",
        "tone",
        "scrape_urls",
        "add_source_urls",
        "verbose_mode",
        "follow_links",
        "subprocess_timeout_minutes",
        "subprocess_retries",
    },
    "dr": {
        "enabled",
        "selected_models",
        "breadth",
        "depth",
        "max_results",
        "concurrency_limit",
        "temperature",
        "max_tokens",
        "timeout",
        "search_provider",
        "enable_caching",
        "follow_links",
        "extract_code",
        "include_images",
        "semantic_search",
        "subprocess_timeout_minutes",
        "subprocess_retries",
    },
    "ma": {
        "enabled",
        "selected_models",
        "max_agents",
        "communication_style",
        "enable_consensus",
        "enable_debate",
        "enable_voting",
        "max_rounds",
    },
    "aiq": {
        "enabled",
        "selected_models",
        "small_model",
        "profile",
        "agent_type",
        "report_min_words",
        "report_max_words",
        "intent_classifier_llm",
        "clarifier_llm",
        "clarifier_planner_llm",
        "shallow_research_llm",
        "orchestrator_llm",
        "researcher_llm",
        "planner_llm",
        "summary_model",
        "data_sources",
        "web_only",
        "preserve_debug_artifacts",
        "job_expiry_seconds",
        "timeout_seconds",
        "config_overrides",
        "advanced_yaml_overrides",
    },
    "eval": {
        "enabled",
        "auto_run",
        "iterations",
        "pairwise_top_n",
        "judge_models",
        "eval_model",
        "timeout_seconds",
        "retries",
        "temperature",
        "max_tokens",
        "thinking_budget_tokens",
        "enable_semantic_similarity",
        "enable_factual_accuracy",
        "enable_coherence",
        "enable_relevance",
        "enable_completeness",
        "enable_citation",
    },
    "pairwise": {
        "enabled",
        "judge_models",
        "judge_model",
    },
    "combine": {
        "enabled",
        "selected_models",
        "model",
        "strategy",
        "max_tokens",
    },
    "launch": {
        "input_source_type",
        "github_connection_id",
        "github_input_paths",
        "github_output_path",
        "output_destination",
        "output_filename_template",
        "github_commit_message",
        "prepend_source_first_line_frontmatter",
        "key_mode",
    },
}

_ALIAS_REWRITES: dict[str, dict[str, str]] = {
    "eval": {"eval_model": "judge_models"},
    "pairwise": {"judge_model": "judge_models"},
    "combine": {"model": "selected_models"},
    "launch": {"key_mode": "(dropped)"},
}

_KNOWN_OVERRIDE_SECTIONS = set(_SECTION_ALLOWED_KEYS.keys())


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit stored preset config_overrides blobs for dead keys and ownership drift.",
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


def _expected_sections_for_key(key: str) -> list[str]:
    return sorted(
        section_name
        for section_name, allowed_keys in _SECTION_ALLOWED_KEYS.items()
        if key in allowed_keys
    )


def _find_wrong_section_paths(raw_overrides: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    for section_name, section_value in sorted(raw_overrides.items()):
        if section_name not in _KNOWN_OVERRIDE_SECTIONS:
            continue
        if not isinstance(section_value, dict):
            continue
        allowed = _SECTION_ALLOWED_KEYS.get(section_name, set())
        for key in sorted(section_value.keys()):
            if key in allowed:
                continue
            expected_sections = _expected_sections_for_key(str(key))
            if not expected_sections:
                continue
            findings.append(
                {
                    "path": f"{section_name}.{key}",
                    "key": key,
                    "actual_section": section_name,
                    "expected_sections": expected_sections,
                }
            )

    return findings


def _find_alias_findings(raw_overrides: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    alias_findings: list[dict[str, Any]] = []
    duplicate_meaning_findings: list[dict[str, Any]] = []

    for section_name, alias_map in sorted(_ALIAS_REWRITES.items()):
        raw_section = raw_overrides.get(section_name)
        if not isinstance(raw_section, dict):
            continue
        for alias_key, canonical_key in sorted(alias_map.items()):
            if alias_key not in raw_section:
                continue
            alias_findings.append(
                {
                    "path": f"{section_name}.{alias_key}",
                    "section": section_name,
                    "alias_key": alias_key,
                    "canonical_key": canonical_key,
                    "rewrite_kind": "dropped" if canonical_key == "(dropped)" else "rewritten",
                }
            )
            if canonical_key != "(dropped)" and canonical_key in raw_section:
                duplicate_meaning_findings.append(
                    {
                        "section": section_name,
                        "alias_key": alias_key,
                        "canonical_key": canonical_key,
                        "path": f"{section_name}.{alias_key}",
                        "canonical_path": f"{section_name}.{canonical_key}",
                    }
                )

    return alias_findings, duplicate_meaning_findings


def _canonical_section_presence(normalized_overrides: dict[str, Any]) -> dict[str, bool]:
    state = derive_canonical_preset_config_state(config_overrides=normalized_overrides or {})
    return {
        "general": bool(state.get("general_config")),
        "concurrency": bool(state.get("concurrency_config")),
        "fpf": bool(state.get("fpf_config")),
        "gptr": bool(state.get("gptr_config")),
        "dr": bool(state.get("dr_config")),
        "ma": bool(state.get("ma_config")),
        "aiq": bool(state.get("aiq_config")),
        "eval": bool(state.get("eval_config")),
        "pairwise": bool(state.get("pairwise_config")),
        "combine": bool(state.get("combine_config")),
        "launch": bool(state.get("launch_config")),
    }


async def _audit_preset(preset: Preset) -> dict[str, Any]:
    raw_overrides = _coerce_json(preset.config_overrides or {})
    raw_override_dict = raw_overrides if isinstance(raw_overrides, dict) else {}
    normalized_overrides = _coerce_json(normalize_config_overrides(preset.config_overrides or {}))
    normalized_override_dict = normalized_overrides if isinstance(normalized_overrides, dict) else {}

    dropped_paths = _find_dropped_key_paths(raw_override_dict, normalized_override_dict)
    wrong_section_findings = _find_wrong_section_paths(raw_override_dict)
    alias_findings, duplicate_meaning_findings = _find_alias_findings(raw_override_dict)

    migration_needed = bool(
        dropped_paths
        or wrong_section_findings
        or alias_findings
        or duplicate_meaning_findings
        or raw_override_dict != normalized_override_dict
    )

    return {
        "preset_id": preset.id,
        "name": preset.name,
        "description": preset.description,
        "created_at": _coerce_json(preset.created_at),
        "updated_at": _coerce_json(preset.updated_at),
        "documents_count": len(preset.documents or []),
        "config_overrides": raw_overrides,
        "normalized_config_overrides": normalized_overrides,
        "canonical_sections_present": _canonical_section_presence(normalized_override_dict),
        "dead_key_findings": dropped_paths,
        "wrong_section_findings": wrong_section_findings,
        "deprecated_alias_findings": alias_findings,
        "duplicate_meaning_findings": duplicate_meaning_findings,
        "migration_needed": migration_needed,
        "stored_equals_normalized": raw_override_dict == normalized_override_dict,
    }


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
            "clean_count": 0,
            "migration_needed_count": 0,
            "dead_key_path_count": 0,
            "wrong_section_count": 0,
            "deprecated_alias_count": 0,
            "duplicate_meaning_count": 0,
            "migration_needed_list": [],
            "dead_key_counts": {},
            "wrong_section_counts": {},
            "deprecated_alias_counts": {},
            "duplicate_meaning_counts": {},
            "presets": [],
        }

        dead_key_counts: Counter[str] = Counter()
        wrong_section_counts: Counter[str] = Counter()
        deprecated_alias_counts: Counter[str] = Counter()
        duplicate_meaning_counts: Counter[str] = Counter()

        for preset in presets:
            preset_result = await _audit_preset(preset)
            report["presets"].append(preset_result)

            if preset_result["migration_needed"]:
                report["migration_needed_count"] += 1
                report["migration_needed_list"].append(
                    {
                        "preset_id": preset_result["preset_id"],
                        "name": preset_result["name"],
                    }
                )
            else:
                report["clean_count"] += 1

            dead_key_counts.update(preset_result["dead_key_findings"])
            wrong_section_counts.update(
                finding["path"] for finding in preset_result["wrong_section_findings"]
            )
            deprecated_alias_counts.update(
                finding["path"] for finding in preset_result["deprecated_alias_findings"]
            )
            duplicate_meaning_counts.update(
                finding["path"] for finding in preset_result["duplicate_meaning_findings"]
            )

        report["dead_key_path_count"] = sum(dead_key_counts.values())
        report["wrong_section_count"] = sum(wrong_section_counts.values())
        report["deprecated_alias_count"] = sum(deprecated_alias_counts.values())
        report["duplicate_meaning_count"] = sum(duplicate_meaning_counts.values())
        report["dead_key_counts"] = dict(sorted(dead_key_counts.items()))
        report["wrong_section_counts"] = dict(sorted(wrong_section_counts.items()))
        report["deprecated_alias_counts"] = dict(sorted(deprecated_alias_counts.items()))
        report["duplicate_meaning_counts"] = dict(sorted(duplicate_meaning_counts.items()))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output_path),
                "preset_count": report["preset_count"],
                "clean_count": report["clean_count"],
                "migration_needed_count": report["migration_needed_count"],
                "dead_key_path_count": report["dead_key_path_count"],
                "wrong_section_count": report["wrong_section_count"],
                "deprecated_alias_count": report["deprecated_alias_count"],
                "duplicate_meaning_count": report["duplicate_meaning_count"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
