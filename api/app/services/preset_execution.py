"""
Shared preset execution validation and runtime config building.

This is the authoritative API-service-owned launch gate for preset-backed runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.aiq import AiqAdapter
from app.adapters.base import GeneratorType as AdapterGeneratorType
from app.infra.db.repositories import ContentRepository
from app.evaluation.criteria import parse_criteria_yaml
from app.services.config_builder import (
    compile_executor_runtime_controls,
    derive_canonical_preset_config_state,
    derive_runtime_config_core,
)
from app.services.compiled_run_config import (
    extract_compiled_run_config_payload,
    hydrate_compiled_run_config,
    serialize_compiled_run_config,
    validate_compiled_run_config_artifact,
)
from app.services.key_mode_resolver import resolve_effective_key_mode
from app.services.preset_validator import PresetValidator
from app.services.run_executor import RunConfig

logger = logging.getLogger(__name__)


class PresetLaunchValidationError(ValueError):
    """Raised when a preset cannot be launched safely."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("Preset launch validation failed: " + "; ".join(errors))


@dataclass
class PresetRuntimeReadiness:
    runnable: bool
    errors: list[str]


def get_static_preset_validation_errors(preset) -> list[str]:
    """Cheap validator for save/load surfaces."""
    return PresetValidator().validate_preset(preset)


async def get_preset_runtime_readiness(
    preset,
    *,
    user: Dict[str, Any],
    db: AsyncSession,
) -> PresetRuntimeReadiness:
    """
    Deeper launch-readiness check used by the explicit runnable endpoint.

    This intentionally does not fetch GitHub input files. GitHub-backed presets
    are only normalized into concrete input documents during run creation.
    """
    state = derive_canonical_preset_config_state(
        config_overrides=getattr(preset, "config_overrides", None) or {},
    )
    launch_cfg = state["launch_config"]

    errors = list(get_static_preset_validation_errors(preset))
    if errors:
        return PresetRuntimeReadiness(runnable=False, errors=errors)

    if launch_cfg.get("input_source_type") == "github":
        github_errors = []
        if not launch_cfg.get("github_connection_id"):
            github_errors.append("github_connection_id is required for GitHub input presets")
        if not launch_cfg.get("github_input_paths"):
            github_errors.append("github_input_paths is required for GitHub input presets")
        return PresetRuntimeReadiness(
            runnable=not github_errors,
            errors=github_errors,
        )

    try:
        snapshot = await build_run_snapshot_from_preset(
            preset,
            user=user,
            db=db,
        )
        await build_executor_config_from_run_snapshot(
            run_id="preset-runnable-check",
            run_config=snapshot,
            preset=preset,
            user=user,
            db=db,
        )
    except PresetLaunchValidationError as exc:
        return PresetRuntimeReadiness(runnable=False, errors=list(exc.errors))
    except Exception as exc:  # pragma: no cover - defensive catch for route safety
        return PresetRuntimeReadiness(runnable=False, errors=[str(exc)])

    return PresetRuntimeReadiness(runnable=True, errors=[])


async def build_run_snapshot_from_preset(
    preset,
    *,
    user: Dict[str, Any],
    db: AsyncSession,
) -> dict[str, Any]:
    """
    Build the persisted run.config snapshot for a direct preset-backed run.

    This mirrors the preset execute path for database/content-library inputs.
    """
    state = derive_canonical_preset_config_state(
        config_overrides=getattr(preset, "config_overrides", None) or {},
    )
    launch_cfg = state["launch_config"]

    errors: list[str] = list(get_static_preset_validation_errors(preset))
    if launch_cfg.get("input_source_type") == "github":
        errors.append(
            "Direct preset execution does not materialize GitHub inputs; create a pending run first."
        )
        logger.warning(
            "[PRESET SNAPSHOT] preset=%s rejected before snapshot build: errors=%s",
            getattr(preset, "id", None),
            errors,
        )
        raise PresetLaunchValidationError(errors)

    content_repo = ContentRepository(db, user_uuid=user["uuid"])

    resolved_documents = await _resolve_input_documents(
        refs=preset.documents or [],
        content_repo=content_repo,
        errors=errors,
    )
    document_contents = {
        doc_id: resolved["content"]
        for doc_id, resolved in resolved_documents.items()
    }

    overrides = state["overrides"]
    general_cfg = overrides.get("general", {})
    fpf_cfg = state["fpf_config"]
    gptr_cfg = state["gptr_config"]
    dr_cfg = state["dr_config"]
    ma_cfg = state["ma_config"]
    aiq_cfg = state["aiq_config"]
    eval_cfg = state["eval_config"]
    pairwise_cfg = state["pairwise_config"]
    combine_cfg = state["combine_config"]
    runtime_controls = compile_executor_runtime_controls(
        eval_config=eval_cfg,
        combine_config=combine_cfg,
        concurrency_config=overrides.get("concurrency"),
        launch_config=launch_cfg,
    )

    generation_config_present = state["generation_config_present"]
    generators = state["generators"]
    if not generators:
        errors.append("At least one generation model must be selected")

    try:
        runtime_core = derive_runtime_config_core(
            generators=generators,
            general_config=general_cfg,
            fpf_config=fpf_cfg,
            gptr_config=gptr_cfg,
            dr_config=dr_cfg,
            aiq_config=aiq_cfg,
            eval_config=eval_cfg,
            pairwise_config=pairwise_cfg,
            combine_config=combine_cfg,
            pairwise_enabled_fallback=state["pairwise_enabled"],
        )
    except ValueError as exc:
        errors.append(str(exc))
        logger.warning(
            "[PRESET SNAPSHOT] preset=%s runtime core derivation failed: errors=%s",
            getattr(preset, "id", None),
            errors,
        )
        raise PresetLaunchValidationError(errors)

    judge_models = runtime_core["judge_models"]
    eval_enabled = runtime_core["eval_enabled"]
    pairwise_enabled = runtime_core["pairwise_enabled"]
    combine_enabled = runtime_core["combine_enabled"]
    combine_models_list = runtime_core["combine_models"]
    combine_strategy = combine_cfg.get("strategy")
    combine_max_tokens = combine_cfg.get("max_tokens")
    logger.info(
        "[PRESET SNAPSHOT] preset=%s input_source_type=%s generators=%s save_run_logs=%s "
        "eval_iterations=%s eval_timeout=%s eval_retries=%s request_timeout=%s "
        "fpf_max_retries=%s fpf_retry_delay=%s",
        getattr(preset, "id", None),
        launch_cfg.get("input_source_type"),
        generators,
        runtime_core["save_run_logs"],
        runtime_controls["eval_iterations"],
        runtime_controls["eval_timeout"],
        runtime_controls["eval_retries"],
        runtime_controls["request_timeout"],
        runtime_controls["fpf_max_retries"],
        runtime_controls["fpf_retry_delay"],
    )

    if eval_enabled and not judge_models:
        errors.append("Evaluation is enabled but no judge models are configured")
    if eval_enabled and eval_cfg.get("iterations") is None:
        errors.append("eval_config.iterations is required")
    if eval_enabled or pairwise_enabled:
        if eval_cfg.get("temperature") is None:
            errors.append("eval_config.temperature is required")
        if eval_cfg.get("max_tokens") is None:
            errors.append("eval_config.max_tokens is required")
        if eval_cfg.get("retries") is None:
            errors.append("eval_config.retries is required")
    if combine_enabled and not combine_strategy:
        errors.append("combine strategy is required when combine is enabled")
    if combine_enabled and not combine_models_list:
        errors.append("combine models are required when combine is enabled")
    if combine_enabled and combine_max_tokens is None:
        errors.append("combine max_tokens is required when combine is enabled")

    if errors:
        logger.warning(
            "[PRESET SNAPSHOT] preset=%s validation failed: errors=%s eval_iterations=%s "
            "eval_timeout=%s eval_retries=%s request_timeout=%s fpf_max_retries=%s "
            "fpf_retry_delay=%s",
            getattr(preset, "id", None),
            errors,
            runtime_controls["eval_iterations"],
            runtime_controls["eval_timeout"],
            runtime_controls["eval_retries"],
            runtime_controls["request_timeout"],
            runtime_controls["fpf_max_retries"],
            runtime_controls["fpf_retry_delay"],
        )
        raise PresetLaunchValidationError(errors)

    snapshot = {
        "document_ids": list(document_contents.keys()),
        "generators": list(generators),
        "models": list(runtime_core["model_settings"].values()),
        "iterations": derive_preset_iterations(preset),
        "save_run_logs": runtime_core["save_run_logs"],
        "post_combine_top_n": general_cfg.get("post_combine_top_n"),
        "expose_criteria_to_generators": general_cfg.get("expose_criteria_to_generators", False),
        "evaluation_enabled": eval_enabled,
        "pairwise_enabled": pairwise_enabled,
        "gptr_config": gptr_cfg if gptr_cfg else None,
        "fpf_config": fpf_cfg if fpf_cfg else None,
        "dr_config": dr_cfg if dr_cfg else None,
        "ma_config": ma_cfg if ma_cfg else None,
        "aiq_config": aiq_cfg if aiq_cfg else None,
        "generation_instructions_id": preset.generation_instructions_id,
        "single_eval_instructions_id": preset.single_eval_instructions_id,
        "pairwise_eval_instructions_id": preset.pairwise_eval_instructions_id,
        "eval_criteria_id": preset.eval_criteria_id,
        "combine_instructions_id": preset.combine_instructions_id,
        "eval_config": eval_cfg if eval_cfg else None,
        "pairwise_config": pairwise_cfg if pairwise_cfg else None,
        "combine_config": combine_cfg if combine_cfg else None,
        "general_config": general_cfg if general_cfg else None,
        "concurrency_config": overrides.get("concurrency") or None,
        "launch_config": launch_cfg if launch_cfg else None,
        "config_overrides": overrides,
        "tags": list(getattr(preset, "tags", []) or []),
    }
    snapshot["compiled_config"] = await build_compiled_run_config_from_run_snapshot(
        run_id="preset-snapshot-compile",
        run_config=snapshot,
        preset=preset,
        user=user,
        db=db,
    )
    return snapshot


async def build_compiled_run_config_from_run_snapshot(
    *,
    run_id: str,
    run_config: dict[str, Any],
    preset,
    user: Dict[str, Any],
    db: AsyncSession,
) -> dict[str, Any]:
    """
    Compile the mixed run snapshot into one frozen execution artifact.
    """
    executor_config, _document_contents = await _build_executor_config_from_snapshot_for_compile(
        run_id=run_id,
        run_config=run_config,
        preset=preset,
        user=user,
        db=db,
    )
    compiled = serialize_compiled_run_config(executor_config)
    logger.info(
        "[COMPILED CONFIG] Built compiled_config for run=%s version=%s hash=%s",
        run_id,
        compiled.get("version"),
        str(compiled.get("hash") or "")[:12],
    )
    return compiled


async def build_executor_config_from_run_snapshot(
    *,
    run_id: str,
    run_config: dict[str, Any],
    preset,
    user: Dict[str, Any],
    db: AsyncSession,
) -> tuple[RunConfig, dict[str, str]]:
    """
    Build the executable RunConfig from a persisted run snapshot plus preset.
    """
    compiled_artifact = run_config.get("compiled_config")
    if compiled_artifact is None:
        raise PresetLaunchValidationError(
            [
                "run is missing compiled_config and cannot be executed; legacy run snapshots are no longer supported"
            ]
        )
    try:
        compiled_payload = validate_compiled_run_config_artifact(compiled_artifact)
        executor_config = hydrate_compiled_run_config(compiled_payload)
    except Exception as exc:
        raise PresetLaunchValidationError([f"compiled_config invalid: {exc}"])
    logger.info(
        "[COMPILED CONFIG] Using compiled_config for run=%s hash=%s request_timeout=%s "
        "eval_timeout=%s eval_retries=%s fpf_max_retries=%s fpf_retry_delay=%s",
        run_id,
        str((compiled_artifact or {}).get("hash") or "")[:12],
        executor_config.request_timeout,
        executor_config.eval_timeout,
        executor_config.eval_retries,
        executor_config.fpf_max_retries,
        executor_config.fpf_retry_delay,
    )
    return executor_config, dict(executor_config.document_contents)


async def _build_executor_config_from_snapshot_for_compile(
    *,
    run_id: str,
    run_config: dict[str, Any],
    preset,
    user: Dict[str, Any],
    db: AsyncSession,
) -> tuple[RunConfig, dict[str, str]]:
    """Compile a truthful RunConfig from the canonical run snapshot."""
    content_repo = ContentRepository(db, user_uuid=user["uuid"])

    errors: list[str] = []
    resolved_documents = await _resolve_input_documents(
        refs=run_config.get("document_ids") or [],
        content_repo=content_repo,
        errors=errors,
    )
    document_contents = {
        doc_id: resolved["content"]
        for doc_id, resolved in resolved_documents.items()
    }
    document_names = {
        doc_id: resolved["name"]
        for doc_id, resolved in resolved_documents.items()
    }
    document_relative_paths = {
        doc_id: resolved["relative_path"]
        for doc_id, resolved in resolved_documents.items()
        if resolved["relative_path"]
    }

    state = derive_canonical_preset_config_state(
        config_overrides=run_config.get("config_overrides") or {},
    )
    config_overrides = state["overrides"]
    combine_config = state["combine_config"]
    eval_config = state["eval_config"]
    pairwise_config = state["pairwise_config"]
    launch_config = run_config.get("launch_config", {}) or config_overrides.get("launch", {}) or {}
    fpf_config = state["fpf_config"]
    gptr_config = state["gptr_config"]
    dr_config = state["dr_config"]
    ma_config = state["ma_config"]
    aiq_config = state["aiq_config"]
    generators = state["generators"]

    if "fpf" in generators:
        instructions = await _load_required_content_body(
            content_repo,
            run_config.get("generation_instructions_id"),
            label="generation instructions",
            errors=errors,
        )
    else:
        instructions = await _load_optional_content_body(
            content_repo,
            run_config.get("generation_instructions_id"),
            label="generation instructions",
            errors=errors,
        )
    single_eval_instructions = await _load_optional_content_body(
        content_repo,
        run_config.get("single_eval_instructions_id"),
        label="single eval instructions",
        errors=errors,
    )
    pairwise_eval_instructions = await _load_optional_content_body(
        content_repo,
        run_config.get("pairwise_eval_instructions_id"),
        label="pairwise eval instructions",
        errors=errors,
    )
    eval_criteria = await _load_optional_content_body(
        content_repo,
        run_config.get("eval_criteria_id"),
        label="eval criteria",
        errors=errors,
    )
    combine_instructions = await _load_optional_content_body(
        content_repo,
        run_config.get("combine_instructions_id"),
        label="combine instructions",
        errors=errors,
    )

    general_config = run_config.get("general_config", {}) or config_overrides.get("general", {}) or {}
    try:
        runtime_core = derive_runtime_config_core(
            generators=generators,
            general_config=state["general_config"] or general_config,
            fpf_config=fpf_config,
            gptr_config=gptr_config,
            dr_config=dr_config,
            aiq_config=aiq_config,
            eval_config=eval_config,
            pairwise_config=pairwise_config,
            combine_config=combine_config,
            pairwise_enabled_fallback=state["pairwise_enabled"],
            save_run_logs_override=run_config.get("save_run_logs"),
        )
    except ValueError as exc:
        errors.append(str(exc))
        raise PresetLaunchValidationError(errors)

    model_settings = runtime_core["model_settings"]
    model_names = runtime_core["model_names"]
    judge_models = runtime_core["judge_models"]
    eval_enabled = runtime_core["eval_enabled"]
    pairwise_enabled = runtime_core["pairwise_enabled"]
    combine_enabled = runtime_core["combine_enabled"]
    combine_models_list = runtime_core["combine_models"]
    concurrency_config = run_config.get("concurrency_config", {}) or config_overrides.get("concurrency", {}) or {}
    runtime_controls = compile_executor_runtime_controls(
        eval_config=eval_config,
        combine_config=combine_config,
        concurrency_config=concurrency_config,
        launch_config=launch_config,
    )
    eval_temperature = runtime_controls["eval_temperature"]
    eval_max_tokens = runtime_controls["eval_max_tokens"]
    eval_thinking_budget_tokens = runtime_controls["eval_thinking_budget_tokens"]
    eval_iterations = runtime_controls["eval_iterations"]
    eval_timeout = runtime_controls["eval_timeout"]
    eval_retries = runtime_controls["eval_retries"]
    combine_strategy = runtime_controls["combine_strategy"]
    combine_max_tokens = runtime_controls["combine_max_tokens"]
    gen_concurrency = runtime_controls["generation_concurrency"]
    eval_concurrency_val = runtime_controls["eval_concurrency"]
    request_timeout = runtime_controls["request_timeout"]
    fpf_max_retries = runtime_controls["fpf_max_retries"]
    fpf_retry_delay = runtime_controls["fpf_retry_delay"]
    logger.info(
        "[SNAPSHOT COMPILE] run=%s preset=%s generators=%s save_run_logs=%s eval_iterations=%s "
        "eval_timeout=%s eval_retries=%s request_timeout=%s fpf_max_retries=%s "
        "fpf_retry_delay=%s",
        run_id,
        getattr(preset, "id", None),
        generators,
        runtime_core["save_run_logs"],
        eval_iterations,
        eval_timeout,
        eval_retries,
        request_timeout,
        fpf_max_retries,
        fpf_retry_delay,
    )

    if eval_retries is None:
        errors.append("preset.eval_retries must be set in preset")
    if eval_enabled or pairwise_enabled:
        if eval_temperature is None:
            errors.append("eval_config.temperature must be set in preset")
        if eval_max_tokens is None:
            errors.append("eval_config.max_tokens must be set in preset")
    if combine_enabled and not combine_strategy:
        errors.append("combine strategy must be set in preset")
    if combine_enabled and combine_max_tokens is None:
        errors.append("combine max_tokens must be set in preset")
    if eval_criteria and (eval_enabled or pairwise_enabled):
        try:
            parse_criteria_yaml(eval_criteria)
        except Exception as exc:
            errors.append(f"eval criteria invalid: {exc}")
    if "aiq" in generators:
        aiq_adapter = AiqAdapter()
        if not await aiq_adapter.health_check():
            errors.append("AI-Q service is unavailable or unhealthy")

    if errors:
        logger.warning(
            "[SNAPSHOT COMPILE] run=%s preset=%s validation failed: errors=%s eval_iterations=%s "
            "eval_timeout=%s eval_retries=%s request_timeout=%s fpf_max_retries=%s "
            "fpf_retry_delay=%s",
            run_id,
            getattr(preset, "id", None),
            errors,
            eval_iterations,
            eval_timeout,
            eval_retries,
            request_timeout,
            fpf_max_retries,
            fpf_retry_delay,
        )
        raise PresetLaunchValidationError(errors)

    key_mode_resolution = await resolve_effective_key_mode(
        db,
        user["uuid"],
        use_byok_first=bool(general_config.get("use_byok_first", False)),
    )

    executor_config = RunConfig(
        user_uuid=user["uuid"],
        document_ids=list(document_contents.keys()),
        document_contents=document_contents,
        instructions=instructions,
        generators=[AdapterGeneratorType(g) for g in generators],
        models=model_names,
        model_settings=model_settings,
        fpf_models=runtime_core["fpf_model_keys"],
        gptr_models=runtime_core["gptr_model_keys"],
        dr_models=runtime_core["dr_model_keys"],
        aiq_models=runtime_core["aiq_model_keys"],
        general_config=general_config,
        fpf_config=fpf_config,
        gptr_config=gptr_config,
        dr_config=dr_config,
        ma_config=ma_config,
        aiq_config=aiq_config,
        eval_config=eval_config,
        pairwise_config=pairwise_config,
        combine_config=combine_config,
        concurrency_config=run_config.get("concurrency_config", {}) or config_overrides.get("concurrency", {}) or {},
        iterations=run_config.get("iterations", 1),
        enable_single_eval=eval_enabled,
        enable_pairwise=pairwise_enabled,
        eval_iterations=eval_iterations,
        eval_judge_models=judge_models,
        eval_retries=eval_retries,
        eval_temperature=eval_temperature,
        eval_max_tokens=eval_max_tokens,
        eval_thinking_budget_tokens=eval_thinking_budget_tokens,
        eval_timeout=eval_timeout,
        pairwise_top_n=runtime_controls["pairwise_top_n"],
        single_eval_instructions=single_eval_instructions,
        pairwise_eval_instructions=pairwise_eval_instructions,
        eval_criteria=eval_criteria,
        enable_combine=combine_enabled,
        combine_strategy=combine_strategy,
        combine_models=combine_models_list,
        combine_instructions=combine_instructions,
        combine_max_tokens=combine_max_tokens,
        post_combine_top_n=run_config.get("post_combine_top_n"),
        expose_criteria_to_generators=run_config.get("expose_criteria_to_generators", False),
        save_run_logs=runtime_core["save_run_logs"],
        generation_concurrency=gen_concurrency,
        eval_concurrency=eval_concurrency_val,
        request_timeout=request_timeout,
        fpf_max_retries=fpf_max_retries,
        fpf_retry_delay=fpf_retry_delay,
        document_names=document_names,
        document_relative_paths=document_relative_paths,
        output_destination=runtime_controls["output_destination"],
        output_filename_template=runtime_controls["output_filename_template"],
        github_connection_id=runtime_controls["github_connection_id"],
        github_output_path=runtime_controls["github_output_path"],
        prepend_source_first_line_frontmatter=runtime_controls["prepend_source_first_line_frontmatter"],
        key_mode=key_mode_resolution.key_mode,
        preset_name=preset.name,
    )

    return executor_config, document_contents


async def _load_required_content_body(
    content_repo: ContentRepository,
    content_id: Optional[str],
    *,
    label: str,
    errors: list[str],
) -> str:
    if not content_id:
        errors.append(f"{label} is required")
        return ""
    content = await content_repo.get_by_id(content_id)
    if not content or not content.body:
        errors.append(f"{label} not found or empty (id={content_id})")
        return ""
    return content.body


async def _load_optional_content_body(
    content_repo: ContentRepository,
    content_id: Optional[str],
    *,
    label: str,
    errors: list[str],
) -> Optional[str]:
    if not content_id:
        return None
    content = await content_repo.get_by_id(content_id)
    if not content or not content.body:
        errors.append(f"{label} not found or empty (id={content_id})")
        return None
    return content.body


async def _resolve_input_documents(
    *,
    refs: list[str],
    content_repo: ContentRepository,
    errors: list[str],
) -> dict[str, dict[str, Optional[str]]]:
    """
    Resolve preset/run input refs against Content Library INPUT_DOCUMENT items only.
    """
    resolved: dict[str, dict[str, Optional[str]]] = {}

    for doc_ref in refs:
        content = await content_repo.get_by_id(doc_ref)
        if content and content.content_type == "input_document":
            if content.body:
                resolved[content.id] = {
                    "content": content.body,
                    "name": content.name,
                    "relative_path": (
                        content.variables.get("github_relative_path")
                        if content.variables
                        else None
                    ),
                }
            else:
                errors.append(f"Input document {content.name} ({content.id}) has no body")
            continue

        if content:
            errors.append(
                f"Input reference {doc_ref} is content type '{content.content_type}', not input_document"
            )
        else:
            errors.append(
                f"Input document reference not found in Content Library: {doc_ref}"
            )

    return resolved


def derive_preset_iterations(preset) -> int:
    general_cfg = (getattr(preset, "config_overrides", None) or {}).get("general", {})
    return general_cfg.get("iterations", 1)
