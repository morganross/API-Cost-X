"""
Presets API Routes.

Endpoints for managing saved preset configurations.
"""
import json
import logging
from datetime import datetime
from types import SimpleNamespace
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Dict, Any

from app.infra.db.session import get_user_db
from app.infra.db.repositories import PresetRepository, RunRepository, ContentRepository
from app.auth.middleware import get_current_user
from app.services.run_executor import RunConfig
from app.services.preset_execution import (
    PresetLaunchValidationError,
    build_executor_config_from_run_snapshot,
    build_run_snapshot_from_preset,
    get_preset_runtime_readiness,
    get_static_preset_validation_errors,
)
from app.services.config_builder import (
    compile_executor_runtime_controls,
    derive_canonical_preset_config_state,
    normalize_aiq_config,
    normalize_combine_config,
    normalize_config_overrides,
    normalize_eval_config,
    normalize_generation_config,
    normalize_launch_config,
    normalize_pairwise_config,
)
from ..schemas.presets import (
    PresetCreate,
    PresetUpdate,
    PresetResponse,
    PresetSummary,
    PresetList,
    PresetRunnableResponse,
)
from ..schemas.runs import (
    # Complete config types
    FpfConfigComplete, GptrConfigComplete, DrConfigComplete, MaConfigComplete, AiqConfigComplete,
    EvalConfigComplete, PairwiseConfigComplete, CombineConfigComplete,
    GeneralConfigComplete, ConcurrencyConfigComplete,
)

from sqlalchemy import inspect
from sqlalchemy.exc import NoInspectionAvailable

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/presets", tags=["presets"])

# Use the canonical execute_run_background from execution.py.
# presets.py previously had its own copy that leaked FileHandlers onto the root logger
# and lacked resume support, durable eval callbacks, and cleanup.
from app.api.routes.runs.execution import execute_run_background


def _get_runs_safely(preset):
    """Safely get runs if loaded, else return empty list."""
    try:
        ins = inspect(preset)
    except NoInspectionAvailable:
        return getattr(preset, "runs", None) or []
    if ins and 'runs' in ins.unloaded:
        return []
    return getattr(preset, "runs", None) or []

def _derive_preset_response_state(preset) -> dict:
    """Derive response/cache fields strictly from canonical config overrides."""
    return derive_canonical_preset_config_state(
        config_overrides=preset.config_overrides or {},
    )


def _derive_launch_scalar_fields(*, overrides: Dict[str, Any], existing=None) -> Dict[str, Any]:
    """Keep launch-critical scalar fields aligned with canonical config_overrides."""
    runtime_controls = compile_executor_runtime_controls(
        eval_config=overrides.get("eval"),
        combine_config=overrides.get("combine"),
        concurrency_config=overrides.get("concurrency"),
        launch_config=overrides.get("launch"),
    )
    general_cfg = overrides.get("general") or {}
    return {
        "generation_concurrency": runtime_controls["generation_concurrency"],
        "eval_concurrency": runtime_controls["eval_concurrency"],
        "request_timeout": runtime_controls["request_timeout"],
        "eval_timeout": runtime_controls["eval_timeout"],
        "fpf_max_retries": runtime_controls["fpf_max_retries"],
        "fpf_retry_delay": runtime_controls["fpf_retry_delay"],
        "eval_retries": runtime_controls["eval_retries"],
        "eval_iterations": runtime_controls["eval_iterations"],
        "post_combine_top_n": general_cfg.get("post_combine_top_n"),
        "input_source_type": runtime_controls["input_source_type"],
        "github_connection_id": runtime_controls["github_connection_id"],
        "github_input_paths": runtime_controls["github_input_paths"],
        "github_output_path": runtime_controls["github_output_path"],
        "output_destination": runtime_controls["output_destination"],
        "output_filename_template": runtime_controls["output_filename_template"],
        "github_commit_message": runtime_controls["github_commit_message"],
        "prepend_source_first_line_frontmatter": runtime_controls["prepend_source_first_line_frontmatter"],
    }


def _build_candidate_preset(*, base=None, **overrides):
    """Build a lightweight preset-like object for validation/preflight."""
    field_names = [
        "id",
        "name",
        "description",
        "documents",
        "config_overrides",
        "generation_instructions_id",
        "single_eval_instructions_id",
        "pairwise_eval_instructions_id",
        "eval_criteria_id",
        "combine_instructions_id",
        "tags",
        "created_at",
        "updated_at",
        "runs",
    ]
    values = {name: getattr(base, name, None) for name in field_names} if base is not None else {}
    values.update(overrides)
    values.setdefault("runs", [])
    return SimpleNamespace(**values)


def _apply_runtime_readiness(preset, readiness) -> None:
    """Attach deeper runtime readiness to a preset object for response rendering."""
    setattr(preset, "_runtime_runnable", readiness.runnable)
    setattr(preset, "_runtime_validation_errors", list(readiness.errors))


def _preset_to_response(preset) -> PresetResponse:
    """Convert DB preset to API response."""
    runs = _get_runs_safely(preset)
    state = _derive_preset_response_state(preset)
    overrides = state["overrides"]
    launch_cfg = normalize_launch_config(overrides.get("launch"), apply_defaults=True)

    # Build complete config objects from overrides
    general_config = None
    if "general" in overrides:
        general_config = GeneralConfigComplete(**overrides["general"])

    fpf_config = None
    if "fpf" in overrides:
        fpf_config = FpfConfigComplete(**overrides["fpf"])

    gptr_config = None
    if "gptr" in overrides:
        gptr_config = GptrConfigComplete(**overrides["gptr"])

    dr_config = None
    if "dr" in overrides:
        dr_config = DrConfigComplete(**overrides["dr"])

    ma_config = None
    if "ma" in overrides:
        ma_config = MaConfigComplete(**overrides["ma"])

    aiq_config = None
    if "aiq" in overrides:
        aiq_config = AiqConfigComplete(**overrides["aiq"])

    eval_config = None
    if "eval" in overrides:
        eval_config = EvalConfigComplete(**overrides["eval"])

    pairwise_config = None
    if "pairwise" in overrides:
        pairwise_config = PairwiseConfigComplete(**overrides["pairwise"])

    combine_config = None
    if "combine" in overrides:
        combine_config = CombineConfigComplete(**overrides["combine"])

    concurrency_config = None
    if "concurrency" in overrides:
        concurrency_config = ConcurrencyConfigComplete(**overrides["concurrency"])

    validation_errors = getattr(preset, "_runtime_validation_errors", None)
    if validation_errors is None:
        validation_errors = get_static_preset_validation_errors(preset)
    runnable = getattr(preset, "_runtime_runnable", None)
    if runnable is None:
        runnable = not validation_errors
    return PresetResponse(
        id=preset.id,
        name=preset.name,
        description=preset.description,
        documents=preset.documents or [],
        # Content Library instruction IDs
        single_eval_instructions_id=preset.single_eval_instructions_id,
        pairwise_eval_instructions_id=preset.pairwise_eval_instructions_id,
        eval_criteria_id=preset.eval_criteria_id,
        combine_instructions_id=preset.combine_instructions_id,
        generation_instructions_id=preset.generation_instructions_id,
        # Complete config objects (NEW)
        general_config=general_config,
        fpf_config=fpf_config,
        gptr_config=gptr_config,
        dr_config=dr_config,
        ma_config=ma_config,
        aiq_config=aiq_config,
        eval_config=eval_config,
        pairwise_config=pairwise_config,
        combine_config=combine_config,
        concurrency_config=concurrency_config,
        # GitHub input source configuration - REQUIRED
        input_source_type=launch_cfg.get("input_source_type"),
        github_connection_id=launch_cfg.get("github_connection_id"),
        github_input_paths=launch_cfg.get("github_input_paths"),
        github_output_path=launch_cfg.get("github_output_path"),
        # Output configuration
        output_destination=launch_cfg.get("output_destination"),
        output_filename_template=launch_cfg.get("output_filename_template"),
        github_commit_message=launch_cfg.get("github_commit_message"),
        prepend_source_first_line_frontmatter=launch_cfg.get("prepend_source_first_line_frontmatter"),
        created_at=preset.created_at,
        updated_at=preset.updated_at,
        run_count=len(runs),
        last_run_at=max((r.created_at for r in runs), default=None) if runs else None,
        runnable=runnable,
        validation_errors=validation_errors,
    )


def _preset_to_summary(preset) -> PresetSummary:
    """Convert DB preset to summary response."""
    runs = _get_runs_safely(preset)
    state = _derive_preset_response_state(preset)
    validation_errors = getattr(preset, "_runtime_validation_errors", None)
    if validation_errors is None:
        validation_errors = get_static_preset_validation_errors(preset)
    runnable = getattr(preset, "_runtime_runnable", None)
    if runnable is None:
        runnable = not validation_errors
    return PresetSummary(
        id=preset.id,
        name=preset.name,
        description=preset.description,
        document_count=len(preset.documents) if preset.documents else 0,
        model_count=len(state["models"]),
        created_at=preset.created_at,
        updated_at=preset.updated_at,
        run_count=len(runs),
        runnable=runnable,
    )


# ============================================================================
# Endpoints
# ============================================================================

@router.post("", response_model=PresetResponse)
async def create_preset(
    data: PresetCreate,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db)
) -> PresetResponse:
    """
    Create a new preset configuration.
    """
    repo = PresetRepository(db, user_uuid=user['uuid'])

    # Check for duplicate name
    existing = await repo.get_by_name(data.name)
    if existing:
        raise HTTPException(status_code=400, detail=f"Preset with name '{data.name}' already exists")

    # Build canonical config_overrides and normalize API-service-owned state.
    config_overrides = {}

    if data.general_config:
        config_overrides["general"] = data.general_config.model_dump()
    if data.fpf_config:
        config_overrides["fpf"] = normalize_generation_config(data.fpf_config.model_dump())
    if data.gptr_config:
        config_overrides["gptr"] = normalize_generation_config(data.gptr_config.model_dump())
    if data.dr_config:
        config_overrides["dr"] = normalize_generation_config(data.dr_config.model_dump())
    if data.ma_config:
        config_overrides["ma"] = normalize_generation_config(data.ma_config.model_dump())
    if data.aiq_config:
        config_overrides["aiq"] = normalize_aiq_config(data.aiq_config.model_dump())
    if data.eval_config:
        config_overrides["eval"] = normalize_eval_config(data.eval_config.model_dump())
    if data.pairwise_config:
        config_overrides["pairwise"] = normalize_pairwise_config(data.pairwise_config.model_dump())
    if data.combine_config:
        config_overrides["combine"] = normalize_combine_config(data.combine_config.model_dump())
    config_overrides["concurrency"] = (
        data.concurrency_config.model_dump()
        if data.concurrency_config
        else ConcurrencyConfigComplete().model_dump()
    )
    config_overrides["launch"] = normalize_launch_config(
        {
            "input_source_type": data.input_source_type or "database",
            "github_connection_id": data.github_connection_id,
            "github_input_paths": data.github_input_paths,
            "github_output_path": data.github_output_path,
            "output_destination": (
                data.output_destination.value
                if hasattr(data.output_destination, "value")
                else data.output_destination
            ),
            "output_filename_template": data.output_filename_template,
            "github_commit_message": data.github_commit_message,
            "prepend_source_first_line_frontmatter": data.prepend_source_first_line_frontmatter,
        },
        apply_defaults=True,
    )

    state = derive_canonical_preset_config_state(
        config_overrides=config_overrides,
    )

    launch_fields = _derive_launch_scalar_fields(overrides=state["overrides"])
    logger.info(
        "[PRESET CREATE] name=%s generators=%s evaluation_enabled=%s pairwise_enabled=%s "
        "save_run_logs=%s generation_concurrency=%s eval_concurrency=%s request_timeout=%s "
        "eval_timeout=%s eval_retries=%s eval_iterations=%s fpf_max_retries=%s "
        "fpf_retry_delay=%s input_source_type=%s output_destination=%s row_scalar_cache_persisted=%s",
        data.name,
        state["generators"],
        state["evaluation_enabled"],
        state["pairwise_enabled"],
        state["save_run_logs"],
        launch_fields["generation_concurrency"],
        launch_fields["eval_concurrency"],
        launch_fields["request_timeout"],
        launch_fields["eval_timeout"],
        launch_fields["eval_retries"],
        launch_fields["eval_iterations"],
        launch_fields["fpf_max_retries"],
        launch_fields["fpf_retry_delay"],
        launch_fields["input_source_type"],
        launch_fields["output_destination"],
        False,
    )
    create_kwargs = dict(
        name=data.name,
        description=data.description,
        documents=data.documents,
        config_overrides=state["overrides"] if state["overrides"] else None,
        # Content Library instruction IDs
        single_eval_instructions_id=data.single_eval_instructions_id,
        pairwise_eval_instructions_id=data.pairwise_eval_instructions_id,
        eval_criteria_id=data.eval_criteria_id,
        combine_instructions_id=data.combine_instructions_id,
        generation_instructions_id=data.generation_instructions_id,
    )
    candidate = _build_candidate_preset(**create_kwargs)
    readiness = await get_preset_runtime_readiness(
        candidate,
        user=user,
        db=db,
    )
    if not readiness.runnable:
        logger.warning(
            "[PRESET CREATE] name=%s blocked by readiness: errors=%s",
            data.name,
            readiness.errors,
        )
        raise HTTPException(
            status_code=400,
            detail={"message": "Preset is not runnable", "errors": readiness.errors},
        )

    preset = await repo.create(**create_kwargs)
    _apply_runtime_readiness(preset, readiness)
    return _preset_to_response(preset)


@router.get("", response_model=PresetList)
async def list_presets(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db)
) -> PresetList:
    """
    List all presets with pagination.
    """
    repo = PresetRepository(db, user_uuid=user['uuid'])

    # Get active (non-deleted) presets
    offset = (page - 1) * page_size
    presets = await repo.get_active(limit=page_size, offset=offset)
    total = await repo.count()
    pages = (total + page_size - 1) // page_size

    items = []
    for preset in presets:
        readiness = await get_preset_runtime_readiness(
            preset,
            user=user,
            db=db,
        )
        _apply_runtime_readiness(preset, readiness)
        items.append(_preset_to_summary(preset))

    return PresetList(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get("/{preset_id}", response_model=PresetResponse)
async def get_preset(
    preset_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db)
) -> PresetResponse:
    """
    Get a specific preset by ID.
    """
    repo = PresetRepository(db, user_uuid=user['uuid'])
    preset = await repo.get_by_id(preset_id)

    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")

    readiness = await get_preset_runtime_readiness(
        preset,
        user=user,
        db=db,
    )
    _apply_runtime_readiness(preset, readiness)
    return _preset_to_response(preset)


@router.put("/{preset_id}", response_model=PresetResponse)
async def update_preset(
    preset_id: str,
    data: PresetUpdate,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db)
) -> PresetResponse:
    """
    Update a preset.
    """
    repo = PresetRepository(db, user_uuid=user['uuid'])
    preset = await repo.get_by_id(preset_id)

    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")

    # Build update dict from non-None values
    update_data = {}

    if data.name is not None:
        # Check for duplicate name (exclude current preset)
        existing = await repo.get_by_name(data.name)
        if existing and existing.id != preset_id:
            raise HTTPException(status_code=400, detail=f"Preset with name '{data.name}' already exists")
        update_data["name"] = data.name

    if data.description is not None:
        update_data["description"] = data.description

    if data.documents is not None:
        update_data["documents"] = data.documents

    # Handle complete config objects (NEW)
    overrides = normalize_config_overrides(preset.config_overrides.copy() if preset.config_overrides else {})

    if data.general_config is not None:
        overrides["general"] = data.general_config.model_dump()

    if data.fpf_config is not None:
        overrides["fpf"] = normalize_generation_config(data.fpf_config.model_dump())
    if data.gptr_config is not None:
        overrides["gptr"] = normalize_generation_config(data.gptr_config.model_dump())
    if data.dr_config is not None:
        overrides["dr"] = normalize_generation_config(data.dr_config.model_dump())
    if data.ma_config is not None:
        overrides["ma"] = normalize_generation_config(data.ma_config.model_dump())
    if data.aiq_config is not None:
        overrides["aiq"] = normalize_aiq_config(data.aiq_config.model_dump())

    if data.eval_config is not None:
        overrides["eval"] = normalize_eval_config(data.eval_config.model_dump())

    if data.pairwise_config is not None:
        overrides["pairwise"] = normalize_pairwise_config(data.pairwise_config.model_dump())

    if data.combine_config is not None:
        overrides["combine"] = normalize_combine_config(data.combine_config.model_dump())

    if data.concurrency_config is not None:
        overrides["concurrency"] = data.concurrency_config.model_dump()

    launch_updates = dict(overrides.get("launch") or {})
    if data.input_source_type is not None:
        launch_updates["input_source_type"] = data.input_source_type
    if data.github_connection_id is not None:
        launch_updates["github_connection_id"] = data.github_connection_id
    if data.github_input_paths is not None:
        launch_updates["github_input_paths"] = data.github_input_paths
    if data.github_output_path is not None:
        launch_updates["github_output_path"] = data.github_output_path
    if data.output_filename_template is not None:
        launch_updates["output_filename_template"] = data.output_filename_template
    if data.output_destination is not None:
        launch_updates["output_destination"] = (
            data.output_destination.value
            if hasattr(data.output_destination, "value")
            else data.output_destination
        )
    if data.github_commit_message is not None:
        launch_updates["github_commit_message"] = data.github_commit_message
    if data.prepend_source_first_line_frontmatter is not None:
        launch_updates["prepend_source_first_line_frontmatter"] = data.prepend_source_first_line_frontmatter
    if launch_updates:
        overrides["launch"] = normalize_launch_config(launch_updates, apply_defaults=True)

    state = derive_canonical_preset_config_state(
        config_overrides=overrides,
    )

    # Handle Content Library instruction IDs
    if data.single_eval_instructions_id is not None:
        update_data["single_eval_instructions_id"] = data.single_eval_instructions_id
    if data.pairwise_eval_instructions_id is not None:
        update_data["pairwise_eval_instructions_id"] = data.pairwise_eval_instructions_id
    if data.eval_criteria_id is not None:
        update_data["eval_criteria_id"] = data.eval_criteria_id
    if data.combine_instructions_id is not None:
        update_data["combine_instructions_id"] = data.combine_instructions_id
    if data.generation_instructions_id is not None:
        update_data["generation_instructions_id"] = data.generation_instructions_id

    # Save config_overrides if modified
    if state["overrides"]:
        update_data["config_overrides"] = state["overrides"]

    launch_fields = _derive_launch_scalar_fields(
        overrides=state["overrides"],
    )
    logger.info(
        "[PRESET UPDATE] preset=%s generators=%s evaluation_enabled=%s pairwise_enabled=%s "
        "save_run_logs=%s generation_concurrency=%s eval_concurrency=%s request_timeout=%s "
        "eval_timeout=%s eval_retries=%s eval_iterations=%s fpf_max_retries=%s "
        "fpf_retry_delay=%s input_source_type=%s output_destination=%s row_scalar_cache_persisted=%s",
        preset_id,
        state["generators"],
        state["evaluation_enabled"],
        state["pairwise_enabled"],
        state["save_run_logs"],
        launch_fields["generation_concurrency"],
        launch_fields["eval_concurrency"],
        launch_fields["request_timeout"],
        launch_fields["eval_timeout"],
        launch_fields["eval_retries"],
        launch_fields["eval_iterations"],
        launch_fields["fpf_max_retries"],
        launch_fields["fpf_retry_delay"],
        launch_fields["input_source_type"],
        launch_fields["output_destination"],
        False,
    )

    launch_relevant_touched = any(
        value is not None
        for value in [
            data.documents,
            data.general_config,
            data.fpf_config,
            data.gptr_config,
            data.dr_config,
            data.ma_config,
            data.aiq_config,
            data.eval_config,
            data.pairwise_config,
            data.combine_config,
            data.concurrency_config,
            data.single_eval_instructions_id,
            data.pairwise_eval_instructions_id,
            data.eval_criteria_id,
            data.combine_instructions_id,
            data.generation_instructions_id,
            data.input_source_type,
            data.github_connection_id,
            data.github_input_paths,
            data.github_output_path,
            data.output_destination,
            data.output_filename_template,
            data.github_commit_message,
            data.prepend_source_first_line_frontmatter,
        ]
    )

    if launch_relevant_touched:
        candidate = _build_candidate_preset(base=preset, **update_data)
        readiness = await get_preset_runtime_readiness(
            candidate,
            user=user,
            db=db,
        )
        if not readiness.runnable:
            logger.warning(
                "[PRESET UPDATE] preset=%s blocked by readiness: errors=%s",
                preset_id,
                readiness.errors,
            )
            raise HTTPException(
                status_code=400,
                detail={"message": "Preset is not runnable", "errors": readiness.errors},
            )

    if update_data:
        preset = await repo.update(preset_id, **update_data)

    readiness = await get_preset_runtime_readiness(
        preset,
        user=user,
        db=db,
    )
    _apply_runtime_readiness(preset, readiness)
    return _preset_to_response(preset)


@router.delete("/{preset_id}")
async def delete_preset(
    preset_id: str,
    permanent: bool = Query(False, description="Permanently delete instead of soft delete"),
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db)
) -> dict:
    """
    Delete a preset.

    By default performs a soft delete. Use permanent=true for hard delete.
    """
    repo = PresetRepository(db, user_uuid=user['uuid'])
    preset = await repo.get_by_id(preset_id)

    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")

    if permanent:
        await repo.delete(preset_id)
        return {"status": "deleted", "preset_id": preset_id, "permanent": True}
    else:
        await repo.soft_delete(preset_id)
        return {"status": "deleted", "preset_id": preset_id, "permanent": False}


@router.post("/{preset_id}/duplicate", response_model=PresetResponse)
async def duplicate_preset(
    preset_id: str,
    new_name: str = Query(..., min_length=1, max_length=200),
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db)
) -> PresetResponse:
    """
    Create a copy of an existing preset with a new name.
    """
    repo = PresetRepository(db, user_uuid=user['uuid'])

    # Check original exists
    original = await repo.get_by_id(preset_id)
    if not original:
        raise HTTPException(status_code=404, detail="Preset not found")

    # Check new name doesn't exist
    existing = await repo.get_by_name(new_name)
    if existing:
        raise HTTPException(status_code=400, detail=f"Preset with name '{new_name}' already exists")

    # Duplicate
    new_preset = await repo.duplicate(preset_id, new_name)
    if not new_preset:
        raise HTTPException(status_code=500, detail="Failed to duplicate preset")

    readiness = await get_preset_runtime_readiness(
        new_preset,
        user=user,
        db=db,
    )
    _apply_runtime_readiness(new_preset, readiness)
    return _preset_to_response(new_preset)


@router.post("/{preset_id}/execute")
async def execute_preset(
    preset_id: str,
    background_tasks: BackgroundTasks,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db)
) -> dict:
    """
    Execute a preset by creating and starting a new run.

    This is a convenience endpoint that:
    1. Creates a new run from the preset configuration
    2. Immediately starts the run

    Returns the created run ID.
    """
    repo = PresetRepository(db, user_uuid=user['uuid'])
    preset = await repo.get_by_id(preset_id)

    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    try:
        run_config_snapshot = await build_run_snapshot_from_preset(
            preset,
            user=user,
            db=db,
        )
    except PresetLaunchValidationError as exc:
        raise HTTPException(status_code=400, detail={"message": "Preset is not runnable", "errors": exc.errors})

    try:
        config, _document_contents = await build_executor_config_from_run_snapshot(
            run_id="preset-execute-preflight",
            run_config=run_config_snapshot,
            preset=preset,
            user=user,
            db=db,
        )
    except PresetLaunchValidationError as exc:
        raise HTTPException(status_code=400, detail={"message": "Preset failed launch validation", "errors": exc.errors})

    # Create a new run from the preset (with config snapshot)
    run_repo = RunRepository(db, user_uuid=user['uuid'])
    run = await run_repo.create(
        preset_id=preset_id,
        title=f"Run from {preset.name} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        description=f"Executed from preset: {preset.name}",
        config=run_config_snapshot,
    )

    # Start the run
    started_run = await run_repo.start(run.id)
    if not started_run:
        raise HTTPException(status_code=500, detail="Failed to start run")

    # Launch background task
    background_tasks.add_task(execute_run_background, run.id, config)

    return {
        "status": "started",
        "run_id": run.id,
        "preset_id": preset_id,
        "preset_name": preset.name,
    }


@router.get("/{preset_id}/runnable", response_model=PresetRunnableResponse)
async def get_preset_runnable(
    preset_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> PresetRunnableResponse:
    repo = PresetRepository(db, user_uuid=user["uuid"])
    preset = await repo.get_by_id(preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")

    readiness = await get_preset_runtime_readiness(
        preset,
        user=user,
        db=db,
    )
    return PresetRunnableResponse(
        preset_id=preset_id,
        runnable=readiness.runnable,
        validation_errors=readiness.errors,
    )
