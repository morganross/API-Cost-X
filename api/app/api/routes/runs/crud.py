"""
CRUD operations for runs.

Endpoints for creating, listing, getting, and deleting runs.
"""
import asyncio
import logging
from datetime import datetime
from typing import Any, Optional, List

from fastapi import APIRouter, HTTPException, Query, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any, Optional, Dict

from app.infra.db.session import get_user_db
from app.infra.db.repositories import RunRepository
from app.auth.middleware import get_current_user, get_optional_user
from app.services.github_input_service import GitHubInputService
from app.services.run_detail_cache import (
    cache_run_detail,
    evict_run_detail,
    evict_user_run_detail_cache,
    get_cached_run_detail,
)
from app.services.config_builder import (
    derive_canonical_preset_config_state,
    normalize_config_overrides,
    normalize_launch_config,
)
from app.services.preset_execution import (
    PresetLaunchValidationError,
    build_compiled_run_config_from_run_snapshot,
)

from ...schemas.runs import (
    RunCreate,
    RunDetail,
    RunList,
    RunSummary,
    RunStatus,
    GeneratorType,
)
from .helpers import to_summary
from .detail_payload import build_run_detail_base, build_source_doc_results
from .interrupted_artifacts import hydrate_interrupted_run_detail_from_artifacts
from app.services.run_finalization_recovery import (
    reconcile_interrupted_finalization,
    reconcile_terminal_row_state,
    reconcile_terminal_task_state,
)
from app.services.run_resumability import build_run_resume_info
from app.infra.db.repositories.run_results import RunResultsRepository
from app.services.results_reader import ResultsReader, RunResultsSnapshot, EvalAggregate
from app.services.run_analytics import build_eval_heatmap_section, build_judge_quality_section
from .detail_payload import build_pairwise_results

logger = logging.getLogger(__name__)
router = APIRouter()

LIVE_SUMMARY_INCLUDE = "live_summary"
TERMINAL_SUMMARY_INCLUDE = "terminal_summary"

def _parse_requested_sections(include: str) -> set[str]:
    return {part.strip() for part in include.split(",") if part.strip()}


def _include_mentions_removed_all_detail(include: str) -> bool:
    return "all_detail" in _parse_requested_sections(include)


def _clamp_limit(value: Optional[int], *, default: int, maximum: int) -> int:
    if value is None:
        return default
    return max(1, min(int(value), maximum))


def _iso_datetime(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat()


def _run_duration_seconds(run: Any) -> Optional[float]:
    started_at = getattr(run, "started_at", None)
    completed_at = getattr(run, "completed_at", None)
    if not started_at or not completed_at:
        return None
    try:
        return max((completed_at - started_at).total_seconds(), 0.0)
    except Exception:
        return None


def _build_section_base_payload(run: Any) -> Dict[str, Any]:
    return {
        "id": run.id,
        "status": run.status,
        "started_at": _iso_datetime(getattr(run, "started_at", None)),
        "completed_at": _iso_datetime(getattr(run, "completed_at", None)),
        "duration_seconds": _run_duration_seconds(run),
    }


def _serialize_timeline_event(row: Any) -> Dict[str, Any]:
    return {
        "phase": getattr(row, "phase", "") or "",
        "event_type": getattr(row, "event_type", "") or "",
        "description": getattr(row, "description", None),
        "model": getattr(row, "model", None),
        "timestamp": _iso_datetime(getattr(row, "occurred_at", None)),
        "duration_seconds": getattr(row, "duration_seconds", None),
        "success": getattr(row, "success", True),
        "source_doc_id": getattr(row, "source_doc_id", None),
    }


def _serialize_pairwise_comparison(row: Any) -> Dict[str, Any]:
    return {
        "doc_id_a": getattr(row, "doc_id_a", "") or "",
        "doc_id_b": getattr(row, "doc_id_b", "") or "",
        "winner": getattr(row, "winner_doc_id", None) or "tie",
        "judge_model": getattr(row, "judge_model", "") or "",
        "trial": getattr(row, "trial", None),
        "reason": getattr(row, "reason", "") or "",
        "score_a": None,
        "score_b": None,
    }


def _detail_value_to_python(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python")
    if isinstance(value, dict):
        return dict(value)
    return value


def _build_interrupted_section_recovery_payload(
    snapshot: RunResultsSnapshot,
    *,
    source_doc_id: Optional[str],
) -> Dict[str, Any]:
    source_doc_results = {
        key: _detail_value_to_python(value)
        for key, value in build_source_doc_results(
            snapshot,
            source_doc_id=source_doc_id,
            include_single_eval_detailed=False,
            include_eval_deviations=False,
            include_timeline_events=False,
            include_pairwise_results=False,
            include_pairwise_comparisons=False,
            include_combined_docs=False,
            include_score_summaries=False,
        ).items()
    }
    return {
        "source_doc_results": source_doc_results,
        "pairwise_results": None,
        "timeline": {"events": []},
        "timeline_events": [],
    }


def _orm_to_dict(obj: Any) -> Dict[str, Any]:
    try:
        cols = {c.key for c in obj.__mapper__.column_attrs}
        return {k: getattr(obj, k) for k in cols}
    except AttributeError:
        if hasattr(obj, "_asdict"):
            return obj._asdict()
        return dict(obj)


async def _repair_run_state_if_needed(
    *,
    db: AsyncSession,
    repo: RunRepository,
    run: Any,
    user_uuid: str,
) -> Any:
    if not hasattr(db, "execute"):
        return run

    repaired_any = False
    run, repaired = await reconcile_interrupted_finalization(db, repo, run)
    if repaired:
        repaired_any = True

    run, repaired = await reconcile_terminal_row_state(repo, run)
    if repaired:
        repaired_any = True

    run, repaired = await reconcile_terminal_task_state(db, repo, run)
    if repaired:
        repaired_any = True

    if repaired_any:
        evict_run_detail(user_uuid=user_uuid, run_id=run.id)

    return run


@router.post("/runs", response_model=RunSummary)
async def create_run(
    data: RunCreate,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db)
) -> RunSummary:
    """
    Create a new run configuration.

    The run starts in PENDING status. Call POST /runs/{id}/start to execute.
    If preset_id is provided, the preset's configuration will be loaded.
    """
    from app.infra.db.repositories import PresetRepository

    repo = RunRepository(db, user_uuid=user['uuid'])
    preset_repo = PresetRepository(db, user_uuid=user['uuid'])

    # Require a preset_id: runs must be created from an existing preset
    if not data.preset_id:
        raise HTTPException(status_code=400, detail="Runs must be created from an existing preset; provide a valid preset_id")

    preset = await preset_repo.get_by_id(data.preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail=f"Preset {data.preset_id} not found")
    logger.info(f"Loading config from preset: {preset.name} (id={data.preset_id})")

    config_overrides = dict(preset.config_overrides or {}) if preset and preset.config_overrides else {}
    state = derive_canonical_preset_config_state(
        config_overrides=config_overrides,
    )
    overrides = state["overrides"]
    launch_cfg = normalize_launch_config(overrides.get("launch"), apply_defaults=True)

    # =========================================================================
    # Handle GitHub Input Source
    # =========================================================================
    # If preset uses GitHub as input source, fetch the files and import them
    document_ids: List[str] = []

    if launch_cfg.get("input_source_type") == "github":
        # Validate GitHub configuration
        if not launch_cfg.get("github_connection_id"):
            raise HTTPException(
                status_code=400,
                detail="Preset uses GitHub input but no github_connection_id is configured"
            )
        if not launch_cfg.get("github_input_paths"):
            raise HTTPException(
                status_code=400,
                detail="Preset uses GitHub input but no github_input_paths are configured"
            )

        # Fetch files from GitHub and import as content
        github_service = GitHubInputService(db, user_uuid=user['uuid'])
        # Collect configured model strings for template-aware skip logic
        _cfg_overrides = normalize_config_overrides(dict(preset.config_overrides or {}))
        _fpf_models = _cfg_overrides.get('fpf', {}).get('selected_models') or []
        _gptr_models = _cfg_overrides.get('gptr', {}).get('selected_models') or []
        _dr_models = _cfg_overrides.get('dr', {}).get('selected_models') or []
        _output_models = (_fpf_models + _gptr_models + _dr_models) or None
        result = await github_service.fetch_and_import(
            connection_id=launch_cfg.get("github_connection_id"),
            paths=launch_cfg.get("github_input_paths"),
            run_id=None,  # Will be set after run is created
            skip_existing_output_path=launch_cfg.get("github_output_path"),
            output_filename_template=launch_cfg.get("output_filename_template"),
            output_models=_output_models,
        )

        if not result.success:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to fetch GitHub input: {result.error}"
            )

        if not result.document_ids:
            # Check if all files were skipped (outputs already exist)
            if result.error and 'already have outputs' in result.error:
                raise HTTPException(
                    status_code=400,
                    detail=f"All input files already have outputs — nothing to run. {result.error}"
                )
            raise HTTPException(
                status_code=400,
                detail=f"No documents found at GitHub paths: {launch_cfg.get('github_input_paths')}"
            )

        document_ids = result.document_ids
        logger.info(f"Fetched {len(document_ids)} documents from GitHub: {[f.name for f in result.files]}")
    else:
        document_ids = list(preset.documents or [])
    general_cfg = state["general_config"]
    eval_cfg = state["eval_config"]
    pairwise_cfg = state["pairwise_config"]
    combine_cfg = state["combine_config"]
    fpf_cfg = state["fpf_config"]
    gptr_cfg = state["gptr_config"]
    dr_cfg = state["dr_config"]
    ma_cfg = state["ma_config"]
    concurrency_cfg = state["concurrency_config"]
    generators = state["generators"]
    models = state["models"]

    if preset:
        logger.info(
            "DEBUG: Canonical post_combine_top_n=%s input_source_type=%s",
            general_cfg.get("post_combine_top_n"),
            launch_cfg.get("input_source_type"),
        )

    config = {
        "document_ids": document_ids,
        "generators": generators,
        "models": models,
        "iterations": general_cfg.get("iterations"),
        "save_run_logs": state["save_run_logs"],
        "post_combine_top_n": general_cfg.get("post_combine_top_n"),
        "expose_criteria_to_generators": general_cfg.get("expose_criteria_to_generators", False),
        "evaluation_enabled": state["evaluation_enabled"],
        "pairwise_enabled": state["pairwise_enabled"],
        "gptr_config": gptr_cfg if "gptr" in overrides else None,
        "fpf_config": fpf_cfg if "fpf" in overrides else None,
        "dr_config": dr_cfg if dr_cfg else None,
        "ma_config": ma_cfg if ma_cfg else None,
        "tags": data.tags,
        "generation_instructions_id": (preset.generation_instructions_id if preset else None) or overrides.get("generation_instructions_id"),
        "single_eval_instructions_id": (preset.single_eval_instructions_id if preset else None) or overrides.get("single_eval_instructions_id"),
        "pairwise_eval_instructions_id": (preset.pairwise_eval_instructions_id if preset else None) or overrides.get("pairwise_eval_instructions_id"),
        "eval_criteria_id": (preset.eval_criteria_id if preset else None) or overrides.get("eval_criteria_id"),
        "combine_instructions_id": (preset.combine_instructions_id if preset else None) or overrides.get("combine_instructions_id"),
        "eval_config": eval_cfg if "eval" in overrides else None,
        "pairwise_config": pairwise_cfg if "pairwise" in overrides else None,
        "combine_config": combine_cfg if "combine" in overrides else None,
        "general_config": general_cfg if "general" in overrides else None,
        "concurrency_config": concurrency_cfg if "concurrency" in overrides else None,
        "launch_config": launch_cfg if "launch" in overrides else None,
        "config_overrides": overrides or None,
    }
    try:
        config["compiled_config"] = await build_compiled_run_config_from_run_snapshot(
            run_id="create-run-compile",
            run_config=config,
            preset=preset,
            user=user,
            db=db,
        )
    except PresetLaunchValidationError as exc:
        logger.error(
            "[COMPILED CONFIG] Refusing create_run without compiled_config. preset_id=%s errors=%s",
            getattr(preset, "id", None),
            exc.errors,
        )
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Run failed launch validation",
                "errors": exc.errors,
            },
        )
    except Exception as exc:
        logger.exception(
            "[COMPILED CONFIG] Unexpected create_run compilation failure. preset_id=%s",
            getattr(preset, "id", None),
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to compile run configuration",
        ) from exc

    run = await repo.create(
        title=data.name,
        description=data.description,
        preset_id=data.preset_id,
        config=config,
        status=RunStatus.PENDING
    )
    return to_summary(run)


@router.get("/runs/count")
async def count_runs(
    status: Optional[str] = Query(None, description="Filter by status"),
    user: Optional[Dict[str, Any]] = Depends(get_optional_user),
) -> dict:
    """Return total number of runs (optionally filtered by status).
    Returns 0 for requests without a selected local run (used by Execute page polling).
    """
    if user is None:
        return {"total": 0, "status": status}
    from app.infra.db.session import get_user_db_session
    async for db in get_user_db_session(user):
        repo = RunRepository(db, user_uuid=user['uuid'])
        total = await repo.count(status=status)
        return {"total": total, "status": status}


@router.get("/runs", response_model=RunList)
async def list_runs(
    status: Optional[str] = Query(None, description="Filter by status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    limit: Optional[int] = Query(None, ge=1, le=500, description="Direct limit (overrides page_size)"),
    offset: Optional[int] = Query(None, ge=0, description="Direct offset (overrides page-based offset)"),
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db)
) -> RunList:
    """
    List all runs with pagination.
    """
    repo = RunRepository(db, user_uuid=user['uuid'])
    # Support direct limit/offset params (used by web GUI) as well as page/page_size
    effective_limit = limit if limit is not None else page_size
    effective_offset = offset if offset is not None else (page - 1) * page_size

    runs = await repo.get_all_for_list(limit=effective_limit, offset=effective_offset, status=status)
    total = await repo.count(status=status)

    items = [to_summary(r) for r in runs]
    pages = (total + effective_limit - 1) // effective_limit

    return RunList(
        items=items,
        total=total,
        page=page,
        page_size=effective_limit,
        pages=pages,
    )


async def _get_run_payload(
    run_id: str,
    *,
    include: str,
    source_doc_id: Optional[str],
    user: Dict[str, Any],
    db: AsyncSession,
) -> Any:
    logger.debug(f"Getting run {run_id}")
    repo = RunRepository(db, user_uuid=user['uuid'])
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    try:
        run = await _repair_run_state_if_needed(
            db=db,
            repo=repo,
            run=run,
            user_uuid=user["uuid"],
        )

        effective_include = include or LIVE_SUMMARY_INCLUDE
        is_live_summary = effective_include == LIVE_SUMMARY_INCLUDE
        safe_terminal = run.status in (
            RunStatus.COMPLETED.value,
            RunStatus.COMPLETED_WITH_ERRORS.value,
        )
        if safe_terminal:
            cached = get_cached_run_detail(
                user_uuid=user["uuid"],
                run_id=run_id,
                include=effective_include,
                source_doc_id=source_doc_id,
            )
            if cached is not None:
                return cached

        requested_sections = set() if is_live_summary else _parse_requested_sections(effective_include)
        needs_task_rows = "tasks" in requested_sections
        if needs_task_rows:
            run_with_tasks = await repo.get_with_tasks(run_id)
            if run_with_tasks is not None:
                run = run_with_tasks

        metadata: dict[str, str] = {}
        try:
            results_repo = RunResultsRepository(db)
            metadata = await results_repo.get_metadata(run_id)
        except Exception:
            logger.warning(
                "[RUN DETAIL] Failed to load normalized metadata for run %s",
                run_id[:8],
                exc_info=True,
            )

        result = build_run_detail_base(run, metadata=metadata)

        try:
            from app.api.routes.runs.execution import _active_executors as _resume_active_executors

            result.resume_info = await build_run_resume_info(
                db=db,
                run=run,
                active_executor_present=bool(_resume_active_executors.get(run_id)),
            )
        except Exception:
            logger.warning(
                "[RUN DETAIL] Failed to build resume_info for run %s",
                run_id[:8],
                exc_info=True,
            )

        if is_live_summary:
            return result

        if effective_include and not is_live_summary:
            from app.api.routes.run_sections import compute_sections

            sections = await compute_sections(
                include=effective_include,
                source_doc_id=source_doc_id,
                db=db,
                user=user,
                run_id=run_id,
            )
            for key, val in sections.items():
                setattr(result, key, val)

        should_hydrate_artifacts = run.status in {
            RunStatus.COMPLETED.value,
            RunStatus.COMPLETED_WITH_ERRORS.value,
            RunStatus.FAILED.value,
            RunStatus.CANCELLED.value,
        }
        if not should_hydrate_artifacts:
            return result

        payload = result.model_dump()
        payload = await asyncio.to_thread(
            hydrate_interrupted_run_detail_from_artifacts,
            payload,
            user_uuid=user["uuid"],
            run_id=run_id,
            source_doc_id=source_doc_id,
        )
        if safe_terminal:
            cache_run_detail(
                user_uuid=user["uuid"],
                run_id=run_id,
                include=effective_include,
                source_doc_id=source_doc_id,
                payload=payload,
            )
        return payload
    except Exception as e:
        logger.exception(f"Error serializing run {run_id}: {e}")
        raise HTTPException(status_code=500, detail="Error retrieving run")


@router.get("/runs/{run_id}/live-summary", response_model=RunDetail)
async def get_run_live_summary(
    run_id: str,
    source_doc_id: Optional[str] = Query(None),
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> Any:
    """Return a lightweight live-status payload for active run polling."""
    return await _get_run_payload(
        run_id,
        include="live_summary",
        source_doc_id=source_doc_id,
        user=user,
        db=db,
    )


@router.get("/runs/{run_id}/snapshot", response_model=RunDetail)
async def get_run_snapshot(
    run_id: str,
    source_doc_id: Optional[str] = Query(None),
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> Any:
    """Return the bounded terminal summary payload for terminal runs."""
    repo = RunRepository(db, user_uuid=user["uuid"])
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status not in (
        RunStatus.COMPLETED.value,
        RunStatus.COMPLETED_WITH_ERRORS.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
    ):
        raise HTTPException(status_code=409, detail="Snapshot is only available for terminal runs")

    return await _get_run_payload(
        run_id,
        include=TERMINAL_SUMMARY_INCLUDE,
        source_doc_id=source_doc_id,
        user=user,
        db=db,
    )


@router.get("/runs/{run_id}/sections/timeline", response_model=Dict[str, Any])
async def get_run_timeline_section(
    run_id: str,
    source_doc_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> Dict[str, Any]:
    """Return a paged timeline section for the run timeline tab."""
    repo = RunRepository(db, user_uuid=user["uuid"])
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    run = await _repair_run_state_if_needed(
        db=db,
        repo=repo,
        run=run,
        user_uuid=user["uuid"],
    )

    effective_limit = _clamp_limit(limit, default=100, maximum=500)
    results_repo = RunResultsRepository(db)
    total_count = await results_repo.count_timeline_events(
        run_id,
        source_doc_id=source_doc_id,
    )
    rows = list(
        await results_repo.get_timeline_events(
            run_id,
            source_doc_id=source_doc_id,
            limit=effective_limit,
            offset=offset,
        )
    )

    payload = _build_section_base_payload(run)
    timeline_events = [_serialize_timeline_event(row) for row in rows]
    if not timeline_events:
        recovered_payload = hydrate_interrupted_run_detail_from_artifacts(
            {"source_doc_results": {}, "pairwise_results": None, "timeline": {"events": []}, "timeline_events": []},
            user_uuid=user["uuid"],
            run_id=run_id,
            source_doc_id=source_doc_id,
        )
        recovered_events = recovered_payload.get("timeline", {}).get("events") or []
        if recovered_events:
            timeline_events = recovered_events[:effective_limit]
            total_count = len(recovered_events)

    payload["timeline"] = {
        "_meta": {
            "total_count": total_count,
            "returned_count": len(timeline_events),
            "offset": offset,
            "limit": effective_limit,
            "has_more": (offset + len(timeline_events)) < total_count,
        },
        "events": timeline_events,
    }
    return payload


@router.get("/runs/{run_id}/sections/llm-calls", response_model=Dict[str, Any])
async def get_run_llm_calls_section(
    run_id: str,
    source_doc_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> Dict[str, Any]:
    """Return a paged LLM-call section for the run timeline tab."""
    repo = RunRepository(db, user_uuid=user["uuid"])
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    run = await _repair_run_state_if_needed(
        db=db,
        repo=repo,
        run=run,
        user_uuid=user["uuid"],
    )

    effective_limit = _clamp_limit(limit, default=50, maximum=200)
    payload = _build_section_base_payload(run)
    payload["llm_calls"] = {
        "_meta": {
            "call_count": 0,
            "returned_count": 0,
            "offset": offset,
            "limit": effective_limit,
            "has_more": False,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
            "total_thinking_tokens": 0,
        },
        "calls": [],
    }
    return payload


@router.get("/runs/{run_id}/sections/pairwise", response_model=Dict[str, Any])
async def get_run_pairwise_section(
    run_id: str,
    source_doc_id: Optional[str] = Query(None),
    comparison_type: Optional[str] = Query(None, pattern="^(pre_combine|post_combine)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> Dict[str, Any]:
    """Return a paged pairwise-comparison section for the source-document pairwise tab."""
    repo = RunRepository(db, user_uuid=user["uuid"])
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    run = await _repair_run_state_if_needed(
        db=db,
        repo=repo,
        run=run,
        user_uuid=user["uuid"],
    )

    effective_limit = _clamp_limit(limit, default=50, maximum=200)
    reader = ResultsReader(db)
    snapshot = await reader.get_run_snapshot_for_datasets(
        run_id,
        datasets={"pairwise_results", "generated_docs", "source_doc_statuses"},
        source_doc_id=source_doc_id,
    )
    pre_summary = build_pairwise_results(snapshot, comparison_type="pre_combine")
    post_summary = build_pairwise_results(snapshot, comparison_type="post_combine")

    if comparison_type == "pre_combine":
        chosen_type = "pre_combine"
        summary = pre_summary
    elif comparison_type == "post_combine":
        chosen_type = "post_combine"
        summary = post_summary
    elif pre_summary is not None:
        chosen_type = "pre_combine"
        summary = pre_summary
    else:
        chosen_type = "post_combine"
        summary = post_summary

    payload = _build_section_base_payload(run)
    if summary is None:
        if comparison_type != "post_combine":
            recovered_payload = hydrate_interrupted_run_detail_from_artifacts(
                _build_interrupted_section_recovery_payload(
                    snapshot,
                    source_doc_id=source_doc_id,
                ),
                user_uuid=user["uuid"],
                run_id=run_id,
                source_doc_id=source_doc_id,
            )
            recovered_pairwise = recovered_payload.get("pairwise_results")
            if recovered_pairwise is None and source_doc_id:
                recovered_source = (recovered_payload.get("source_doc_results") or {}).get(source_doc_id)
                if isinstance(recovered_source, dict):
                    recovered_pairwise = recovered_source.get("pairwise_results")

            if isinstance(recovered_pairwise, dict):
                recovered_comparisons = list(recovered_pairwise.get("comparisons") or [])
                chosen_type = "pre_combine"
                recovered_total = int(recovered_pairwise.get("total_comparisons") or len(recovered_comparisons))
                paged_comparisons = recovered_comparisons[offset:offset + effective_limit]
                payload["pairwise"] = {
                    "_meta": {
                        "comparison_type": chosen_type,
                        "total_count": recovered_total,
                        "returned_count": len(paged_comparisons),
                        "offset": offset,
                        "limit": effective_limit,
                        "has_more": (offset + len(paged_comparisons)) < recovered_total,
                    },
                    "winner_doc_id": recovered_pairwise.get("winner_doc_id"),
                    "rankings": list(recovered_pairwise.get("rankings") or []),
                    "comparisons": paged_comparisons,
                }
                return payload

        payload["pairwise"] = {
            "_meta": {
                "comparison_type": chosen_type,
                "total_count": 0,
                "returned_count": 0,
                "offset": offset,
                "limit": effective_limit,
                "has_more": False,
            },
            "winner_doc_id": None,
            "rankings": [],
            "comparisons": [],
        }
        return payload

    results_repo = RunResultsRepository(db)
    page_rows = list(
        await results_repo.get_pairwise_results(
            run_id,
            source_doc_id=source_doc_id,
            comparison_type=chosen_type,
            limit=effective_limit,
            offset=offset,
        )
    )

    payload["pairwise"] = {
        "_meta": {
            "comparison_type": chosen_type,
            "total_count": summary.total_comparisons,
            "returned_count": len(page_rows),
            "offset": offset,
            "limit": effective_limit,
            "has_more": (offset + len(page_rows)) < summary.total_comparisons,
        },
        "winner_doc_id": summary.winner_doc_id,
        "rankings": [ranking.model_dump() for ranking in (summary.rankings or [])],
        "comparisons": [_serialize_pairwise_comparison(row) for row in page_rows],
    }
    return payload


@router.get("/runs/{run_id}/sections/evaluation", response_model=Dict[str, Any])
async def get_run_evaluation_section(
    run_id: str,
    source_doc_id: str = Query(...),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> Dict[str, Any]:
    """Return the source-document evaluation section without the broader run payload."""
    repo = RunRepository(db, user_uuid=user["uuid"])
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    run = await _repair_run_state_if_needed(
        db=db,
        repo=repo,
        run=run,
        user_uuid=user["uuid"],
    )

    effective_limit = _clamp_limit(limit, default=25, maximum=100)
    results_repo = RunResultsRepository(db)
    total_doc_count = await results_repo.count_generated_docs(run_id, source_doc_id=source_doc_id)
    page_generated_docs = list(
        await results_repo.get_generated_docs(
            run_id,
            source_doc_id=source_doc_id,
            limit=effective_limit,
            offset=offset,
        )
    )
    combined_docs = list(
        await results_repo.get_combined_docs(
            run_id,
            source_doc_id=source_doc_id,
        )
    )
    source_doc_statuses = list(
        await results_repo.get_source_doc_statuses(
            run_id,
            source_doc_id=source_doc_id,
        )
    )
    aggregate_doc_ids = [row.doc_id for row in page_generated_docs if getattr(row, "doc_id", None)]
    aggregate_doc_ids.extend(
        row.doc_id for row in combined_docs if getattr(row, "doc_id", None)
    )
    eval_aggregates = list(
        await results_repo.get_eval_aggregates(
            run_id,
            source_doc_id=source_doc_id,
            doc_ids=aggregate_doc_ids or None,
        )
    )
    snapshot = RunResultsSnapshot(
        generated_docs=[_orm_to_dict(row) for row in page_generated_docs],
        eval_aggregates=[
            EvalAggregate(
                doc_id=row["doc_id"],
                source_doc_id=row["source_doc_id"],
                criterion=row["criterion"],
                judge_model=row["judge_model"],
                avg_score=float(row["avg_score"] or 0),
                trial_count=int(row["trial_count"] or 0),
                reason=row.get("reason") or None,
            )
            for row in eval_aggregates
        ],
        combined_docs=[_orm_to_dict(row) for row in combined_docs],
        source_doc_statuses=[_orm_to_dict(row) for row in source_doc_statuses],
    )
    source_doc_results = build_source_doc_results(
        snapshot,
        source_doc_id=source_doc_id,
        include_single_eval_detailed=False,
        include_timeline_events=False,
        include_eval_deviations=False,
        include_pairwise_results=False,
        include_pairwise_comparisons=False,
        include_generated_docs=True,
        include_combined_docs=True,
        include_score_summaries=True,
    )
    evaluation = source_doc_results.get(source_doc_id)

    payload = _build_section_base_payload(run)
    payload["evaluation"] = evaluation.model_dump(mode="json") if evaluation is not None else None
    payload["evaluation_meta"] = {
        "total_count": total_doc_count,
        "returned_count": len(page_generated_docs),
        "offset": offset,
        "limit": effective_limit,
        "has_more": (offset + len(page_generated_docs)) < total_doc_count,
    }
    return payload


@router.get("/runs/{run_id}/sections/eval-heatmap", response_model=Dict[str, Any])
async def get_run_eval_heatmap_section(
    run_id: str,
    source_doc_id: Optional[str] = Query(None),
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> Dict[str, Any]:
    """Return the source-document evaluation heatmap without the broader run payload."""
    repo = RunRepository(db, user_uuid=user["uuid"])
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    run = await _repair_run_state_if_needed(
        db=db,
        repo=repo,
        run=run,
        user_uuid=user["uuid"],
    )

    reader = ResultsReader(db)
    snapshot = await reader.get_run_snapshot_for_datasets(
        run_id,
        datasets={"generated_docs", "eval_aggregates"},
        source_doc_id=source_doc_id,
    )
    payload = _build_section_base_payload(run)
    payload["eval_heatmap"] = build_eval_heatmap_section(snapshot)
    return payload


@router.get("/runs/{run_id}/sections/judge-quality", response_model=Dict[str, Any])
async def get_run_judge_quality_section(
    run_id: str,
    source_doc_id: Optional[str] = Query(None),
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> Dict[str, Any]:
    """Return the judge-quality analytics section without the broader run payload."""
    repo = RunRepository(db, user_uuid=user["uuid"])
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    run = await _repair_run_state_if_needed(
        db=db,
        repo=repo,
        run=run,
        user_uuid=user["uuid"],
    )

    reader = ResultsReader(db)
    snapshot = await reader.get_run_snapshot_for_datasets(
        run_id,
        datasets={
            "generated_docs",
            "combined_docs",
            "eval_aggregates",
            "eval_scores_raw",
            "pairwise_results",
            "criteria_list",
            "evaluator_list",
        },
        source_doc_id=source_doc_id,
    )
    payload = _build_section_base_payload(run)
    payload["judge_quality"] = build_judge_quality_section(snapshot)
    return payload


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(
    run_id: str,
    request: Request,
    include: str = Query(""),
    source_doc_id: Optional[str] = Query(None),
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db)
) -> Any:
    """
    Get run information with summary-safe defaults.
    """
    if _include_mentions_removed_all_detail(include):
        raise HTTPException(
            status_code=410,
            detail="The all_detail run read has been removed. Use the base run route with bounded includes or explicit section routes.",
        )

    include_present = "include" in request.query_params
    include_raw = request.query_params.get("include") if include_present else None
    if not include_present or include_raw == "":
        logger.info(
            "[RUN DETAIL] Blank base GET /runs/{id} resolved to live_summary "
            "(route=get_run run_id=%s include_present=%s include_raw=%r source_doc_id=%s)",
            run_id,
            include_present,
            include_raw,
            source_doc_id,
        )
    return await _get_run_payload(
        run_id,
        include=include or LIVE_SUMMARY_INCLUDE,
        source_doc_id=source_doc_id,
        user=user,
        db=db,
    )


@router.delete("/runs/bulk")
async def bulk_delete_runs(
    target: str = Query(..., pattern="^(failed|completed_failed)$", description="failed or completed_failed"),
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db)
) -> dict:
    """Bulk delete runs by status groups."""
    repo = RunRepository(db, user_uuid=user['uuid'])
    evict_user_run_detail_cache(user["uuid"])
    if target == "failed":
        statuses = [RunStatus.FAILED.value, RunStatus.CANCELLED.value]
    else:
        statuses = [RunStatus.FAILED.value, RunStatus.COMPLETED.value, RunStatus.CANCELLED.value]
    deleted = await repo.bulk_delete_by_status(statuses)
    return {"status": "ok", "deleted": deleted, "target": target}


@router.delete("/runs/{run_id}")
async def delete_run(
    run_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db)
) -> dict:
    """
    Delete a run.

    Only allowed for runs in PENDING, COMPLETED, FAILED, or CANCELLED status.
    """
    repo = RunRepository(db, user_uuid=user['uuid'])
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    if run.status == RunStatus.RUNNING:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete a running run. Cancel it first."
        )

    evict_run_detail(user_uuid=user["uuid"], run_id=run_id)
    await repo.delete(run_id)
    return {"status": "deleted", "run_id": run_id}
