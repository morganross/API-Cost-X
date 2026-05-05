"""
Run lifecycle execution control.

Endpoints for starting, pausing, resuming, and cancelling runs.
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any

from app.infra.db.session import get_user_db, get_user_session_by_uuid
from app.auth.middleware import get_current_user
from app.infra.db.repositories import RunRepository, TaskRepository
from app.services.preset_execution import (
    PresetLaunchValidationError,
    build_executor_config_from_run_snapshot,
)
from app.services.run_executor import RunConfig, RunExecutor, RunPhase
from app.utils.logging_utils import get_run_logger
from app.utils.paths import get_user_log_db_path
from app.evaluation.models import SingleEvalResult
from app.services.log_writer import RunLogWriter
from app.services.run_finalization_recovery import (
    clear_finalization_markers,
    write_finalization_marker,
)
from app.services.run_detail_cache import evict_run_detail
from app.services.run_resumability import build_run_resume_info

from ...schemas.runs import RunResumeInfo, RunStatus
logger = logging.getLogger(__name__)
router = APIRouter()

# Track active executors for cancellation support
_active_executors: Dict[str, RunExecutor] = {}


def _is_sqlite_lock_error(exc: BaseException) -> bool:
    """Return True for transient SQLite lock errors."""
    current: Optional[BaseException] = exc
    while current is not None:
        message = str(current).lower()
        if "database is locked" in message or "database table is locked" in message:
            return True
        current = current.__cause__ or current.__context__
    return False


async def _rollback_quietly(session: Any) -> None:
    rollback = getattr(session, "rollback", None)
    if not callable(rollback):
        return
    try:
        result = rollback()
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        logger.debug("Rollback after transient DB lock failed", exc_info=True)


async def _retry_sqlite_locked_write(
    label: str,
    operation,
    *,
    session: Any = None,
    attempts: int = 6,
    initial_delay_seconds: float = 0.25,
) -> Any:
    """Retry short-lived SQLite write locks without downgrading a good run."""
    delay = initial_delay_seconds
    last_error: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except Exception as exc:
            if not _is_sqlite_lock_error(exc):
                raise
            last_error = exc
            if session is not None:
                await _rollback_quietly(session)
            if attempt >= attempts:
                break
            logger.warning(
                "[DB RETRY] %s hit SQLite lock on attempt %d/%d; retrying in %.2fs",
                label,
                attempt,
                attempts,
                delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 2.0)
    assert last_error is not None
    raise last_error


def _row_attr(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _build_completed_eval_cache(
    eval_scores: list[Any],
    *,
    expected_criteria_count: int = 0,
) -> Dict[str, set[tuple[str, int]]]:
    attempts_by_doc: Dict[str, set[tuple[str, int]]] = {}
    criteria_by_attempt: Dict[tuple[str, str, int], set[str]] = {}

    for row in eval_scores:
        doc_id = str(_row_attr(row, "doc_id", "") or "")
        judge_model = str(_row_attr(row, "judge_model", "") or "")
        trial = int(_row_attr(row, "trial", 1) or 1)
        criterion = str(_row_attr(row, "criterion", "") or "")
        if not doc_id or not judge_model:
            continue
        attempt_key = (doc_id, judge_model, trial)
        criteria_by_attempt.setdefault(attempt_key, set())
        if criterion:
            criteria_by_attempt[attempt_key].add(criterion)

    for (doc_id, judge_model, trial), criteria in criteria_by_attempt.items():
        if expected_criteria_count > 0 and len(criteria) < expected_criteria_count:
            continue
        attempts_by_doc.setdefault(doc_id, set()).add((judge_model, trial))

    return attempts_by_doc


async def execute_run_background(run_id: str, config: RunConfig):
    """
    Background task to execute a run and update DB.
    """
    save_run_logs = bool(getattr(config, "save_run_logs", True))

    # Sidecar log writer — EVENT/DETAIL entries to local logs.db
    log_writer = RunLogWriter(config.user_uuid, run_id, save_to_sidecar=save_run_logs)
    _pending_db_key_evict_reason: Optional[str] = None

    # Size monitoring — warn if sidecar DB exceeds 100MB (retention = forever)
    if save_run_logs:
        _log_db_path = get_user_log_db_path(config.user_uuid)
        if _log_db_path.exists() and _log_db_path.stat().st_size > 100_000_000:
            logger.warning("Sidecar log DB for user %s exceeds 100MB: %d bytes",
                           config.user_uuid[:8], _log_db_path.stat().st_size)

    run_logger = get_run_logger(
        run_id,
        log_writer=log_writer,
        capture_details=save_run_logs,
    )

    run_logger.info(
        "Starting background execution for run %s; save_run_logs=%s",
        run_id,
        save_run_logs,
    )
    run_logger.info(
        "Run %s runtime controls: generators=%s request_timeout=%s eval_timeout=%s "
        "eval_retries=%s fpf_max_retries=%s fpf_retry_delay=%s key_mode=%s",
        run_id,
        [generator.value for generator in getattr(config, "generators", [])],
        getattr(config, "request_timeout", None),
        getattr(config, "eval_timeout", None),
        getattr(config, "eval_retries", None),
        getattr(config, "fpf_max_retries", None),
        getattr(config, "fpf_retry_delay", None),
        getattr(config, "key_mode", None),
    )

    await log_writer.event("apicostx", "INFO", "run_start",
                           f"Run {run_id} started: adapter={getattr(config, 'generators', ['?'])[0].value if config.generators else '?'}, save_run_logs={str(save_run_logs).lower()}")

    try:
        # Create a fresh executor instance for this run
        executor = RunExecutor(run_logger=run_logger)
        prev_executor = _active_executors.get(run_id)
        _active_executors[run_id] = executor
        logger.debug(f"Registered executor for run {run_id}; previous_executor_exists={bool(prev_executor)}")

        # Shared state for callbacks (protected by lock)
        db_lock = asyncio.Lock()
        # _doc_eval_running: {doc_id: (score_sum, score_count)} — one running average per doc.
        # Kilobytes of RAM for any realistic run. Used ONLY by _get_all_eval_scores() so the
        # executor can pick top-N docs for pairwise selection. NOT used for result storage.
        _doc_eval_running: dict[str, tuple[float, int]] = {}
        gen_doc_to_source_doc: dict[str, str] = {}  # doc_id → source_doc_id (needed by eval callbacks)
        # Track evaluator and criterion sets so the UI can render per-judge, per-criterion badges while the run is live
        all_evaluators_incremental: set[str] = set()
        all_criteria_incremental: set[str] = set()
        eval_count = 0
        gen_count = 0

        # Pre-populate resume state from DB.
        # Rebuilds _doc_eval_running, gen_doc_to_source_doc, and criteria/evaluator sets from
        # existing rows so the pipeline can skip already-completed work on resume.
        _resume_eval_scores: list = []
        try:
            async with get_user_session_by_uuid(config.user_uuid) as _pre_session:
                from app.infra.db.repositories.run_results import RunResultsRepository as _ResumeRR
                _rr = _ResumeRR(_pre_session)

                _resume_eval_scores_raw = await _rr.get_eval_scores(run_id)
                _gen_docs = await _rr.get_generated_docs(run_id)

                if _resume_eval_scores_raw:
                    _resume_eval_scores = _resume_eval_scores_raw
                    # Rebuild _doc_eval_running (sum, count) from flat score rows
                    for _es in _resume_eval_scores_raw:
                        _cur_sum, _cur_cnt = _doc_eval_running.get(_es.doc_id, (0.0, 0))
                        _doc_eval_running[_es.doc_id] = (_cur_sum + float(_es.score), _cur_cnt + 1)
                        all_evaluators_incremental.add(_es.judge_model)
                        all_criteria_incremental.add(_es.criterion)

                    logger.info(
                        "[RESUME] Pre-loaded eval state from DB: %d docs, %d judges, %d criteria",
                        len(_doc_eval_running),
                        len(all_evaluators_incremental),
                        len(all_criteria_incremental),
                    )
                    await log_writer.event("apicostx", "INFO", "resume_loaded",
                                           f"Pre-loaded eval state: {len(_doc_eval_running)} docs, {len(all_evaluators_incremental)} judges, {len(all_criteria_incremental)} criteria")

                # Rebuild gen_doc_to_source_doc from run_generated_docs
                if _gen_docs:
                    for _gd in _gen_docs:
                        gen_doc_to_source_doc[_gd.doc_id] = _gd.source_doc_id

        except Exception as _pre_err:
            logger.warning("[RESUME] Failed to pre-populate resume state (non-fatal): %s", _pre_err)

        # Build completed_eval_cache so the pipeline can backfill only missing
        # judge/trial attempts for already-generated docs.
        if _resume_eval_scores:
            _expected_criteria_count = 0
            try:
                if getattr(config, "eval_criteria", None):
                    from app.evaluation.criteria import parse_criteria_yaml as _parse_criteria_yaml

                    _expected_criteria_count = len(_parse_criteria_yaml(config.eval_criteria))
            except Exception as _criteria_err:
                logger.warning("[RESUME] Failed to parse eval criteria for cache completeness check: %s", _criteria_err)

            _eval_cache = _build_completed_eval_cache(
                list(_resume_eval_scores),
                expected_criteria_count=_expected_criteria_count,
            )
            if _eval_cache:
                config.completed_eval_cache = _eval_cache
                logger.info("[RESUME] Built completed_eval_cache: %d docs with existing eval attempts", len(_eval_cache))

        async def on_gen_complete(doc_id: str, model: str, generator: str, source_doc_id: str, iteration: int, file_path: Optional[str] = None, duration_seconds: Optional[float] = None, started_at: Optional[datetime] = None):
            """Callback fired after each document generation - writes generated_docs to DB immediately."""
            nonlocal gen_count

            run_logger.info("[on_gen_complete] doc_id=%s model=%s generator=%s source=%s iter=%d",
                            doc_id, model, generator, source_doc_id, iteration)
            logger.info("[on_gen_complete] run=%s doc_id=%s model=%s", run_id[:8], doc_id[:8], model)
            await log_writer.event("apicostx", "INFO", "gen_saved",
                                   f"Gen #{gen_count + 1}: doc_id={doc_id[:8]}, model={model}")

            async with db_lock:
                gen_count += 1

                gen_doc_to_source_doc[doc_id] = source_doc_id

                # Persist to normalized run_generated_docs (SSOT) via shared write helper
                from app.services.run_callbacks import write_gen_doc
                await write_gen_doc(
                    run_id, config.user_uuid, doc_id, model, generator, source_doc_id, iteration,
                    file_path=file_path,
                    duration_seconds=duration_seconds,
                    started_at=started_at,
                )

                # Checkpoint: write a Task row so resume can skip this on restart
                # Guard against duplicates (e.g. if on_gen_complete fires twice)
                try:
                    async with get_user_session_by_uuid(config.user_uuid) as session:
                        task_repo = TaskRepository(session)
                        _existing = await task_repo.find_completed_generation_task(
                            run_id, source_doc_id, model, iteration
                        )
                        if not _existing:
                            task = await task_repo.create_generation_task(
                                run_id=run_id,
                                source_doc_id=source_doc_id,
                                model_name=model,
                                generator=generator,
                                iteration=iteration,
                            )
                            await task_repo.complete_with_output(
                                task.id,
                                output_ref=f"generated/{doc_id}.md",
                            )
                    async with get_user_session_by_uuid(config.user_uuid) as session:
                        run_repo = RunRepository(session, user_uuid=config.user_uuid)
                        await run_repo.increment_completed_tasks(run_id)
                except Exception as _ckpt_err:
                    run_logger.warning("[on_gen_complete] Checkpoint write failed (non-fatal): %s", _ckpt_err)

                run_logger.info("[DB] Saved gen #%d: %s | %s", gen_count, doc_id, model)

        async def on_eval_complete(doc_id: str, judge_model: str, trial: int, result: SingleEvalResult):
            """Callback fired after each individual judge evaluation - writes to DB immediately."""
            nonlocal eval_count

            async with db_lock:
                eval_count += 1

                source_doc_id = gen_doc_to_source_doc.get(doc_id)

                # Update running average for _get_all_eval_scores (kilobytes: one (sum, count) per doc)
                _cur_sum, _cur_cnt = _doc_eval_running.get(doc_id, (0.0, 0))
                for _s in result.scores:
                    _cur_sum += float(_s.score)
                    _cur_cnt += 1
                _doc_eval_running[doc_id] = (_cur_sum, _cur_cnt)

                # Persist eval scores to run_eval_scores (SSOT) via shared write helper.
                # all_criteria_incremental and all_evaluators_incremental are updated in-place
                # by write_eval_scores so the streaming state cache stays consistent.
                from app.services.run_callbacks import write_eval_scores
                await write_eval_scores(
                    run_id, config.user_uuid, doc_id, source_doc_id or "",
                    trial, result,
                    all_criteria_incremental, all_evaluators_incremental,
                )

                # Checkpoint: write a Task row for this eval so resume can skip it
                try:
                    async with get_user_session_by_uuid(config.user_uuid) as session:
                        task_repo = TaskRepository(session)
                        eval_task = await task_repo.create_eval_task(
                            run_id=run_id,
                            source_doc_id=source_doc_id or "",
                            judge_model=judge_model,
                            iteration=trial,
                            phase="single_eval",
                        )
                        await task_repo.complete_with_output(
                            eval_task.id,
                            output_ref=f"eval/{doc_id}_{judge_model}_trial{trial}.json",
                        )
                except Exception as _ckpt_err:
                    run_logger.warning("[on_eval_complete] Checkpoint write failed (non-fatal): %s", _ckpt_err)

                run_logger.info("[DB] Saved eval #%d: %s | %s trial=%d avg=%.2f",
                               eval_count, doc_id, judge_model, trial, result.average_score)
                logger.info("[on_eval_complete] run=%s doc=%s judge=%s trial=%d", run_id[:8], doc_id[:8], judge_model, trial)
                await log_writer.event("apicostx", "INFO", "eval_saved",
                                       f"Eval #{eval_count}: doc_id={doc_id[:8]}, judge={judge_model}, avg={result.average_score:.2f}")

        async def on_gen_cached(doc_id: str, model: str, generator: str, source_doc_id: str, iteration: int):
            """Fired for tasks served from resume cache — only increments the progress counter."""
            try:
                async with get_user_session_by_uuid(config.user_uuid) as session:
                    run_repo = RunRepository(session, user_uuid=config.user_uuid)
                    await run_repo.increment_completed_tasks(run_id)
                run_logger.info("[on_gen_cached] Counter incremented for cached task: %s | %s", doc_id, model)
                await log_writer.event("apicostx", "INFO", "gen_cached", "Generation cache hit, skipping")
            except Exception as _e:
                run_logger.warning("[on_gen_cached] Failed to increment counter: %s", _e)

        # Attach callbacks to config
        config.on_gen_complete = on_gen_complete
        config.on_eval_complete = on_eval_complete
        config.on_gen_cached = on_gen_cached

        # Provide a live getter so pairwise top-N selection can see scores for
        # ALL docs (including cached/resumed ones).
        # Reads from _doc_eval_running: one (sum, count) per doc — kilobytes, not a blob.
        def _get_all_eval_scores() -> dict:
            return {
                doc_id: (s / c) if c > 0 else 0.0
                for doc_id, (s, c) in _doc_eval_running.items()
            }
        config.get_all_eval_scores = _get_all_eval_scores

        # Set total_tasks on the run so progress % is accurate
        try:
            _total_gen_tasks = (
                len(config.document_ids)
                * sum(len(config.get_models_for_generator(g)) for g in config.generators)
                * config.iterations
            )
            async with get_user_session_by_uuid(config.user_uuid) as _tt_session:
                _tt_repo = RunRepository(_tt_session, user_uuid=config.user_uuid)
                await _tt_repo.set_total_tasks(run_id, _total_gen_tasks)
        except Exception as _tt_err:
            logger.warning(f"[execute_run_background] Failed to set total_tasks for run {run_id}: {_tt_err}")

        # Local self-hosted runs always use local provider keys or .env keys.
        config.key_mode = 'byok'

        result = await executor.execute(run_id, config, log_writer=log_writer)

        async def _persist_stable_result_artifacts(
            _result,
            *,
            _now_c: Optional[datetime] = None,
            _session=None,
        ) -> None:
            """Persist idempotent normalized artifacts that are safe to replay."""
            if _session is None:
                async with get_user_session_by_uuid(config.user_uuid) as _stable_session:
                    await _persist_stable_result_artifacts(
                        _result,
                        _now_c=_now_c,
                        _session=_stable_session,
                    )
                return

            from app.infra.db.repositories.run_results import RunResultsRepository as _CRR

            _persisted_at = _now_c or datetime.utcnow()
            _crr = _CRR(_session)
            _generated_by_doc = {
                _gd.doc_id: _gd for _gd in (_result.generated_docs or [])
            }
            _combined_by_doc = {
                _cd.doc_id: _cd for _cd in (_result.combined_docs or [])
            }

            def _source_doc_id_for(_doc_id: Optional[str]) -> str:
                if not _doc_id:
                    return ""
                _generated = _generated_by_doc.get(_doc_id)
                if _generated and getattr(_generated, "source_doc_id", None):
                    return _generated.source_doc_id
                _combined = _combined_by_doc.get(_doc_id)
                if _combined and getattr(_combined, "source_doc_id", None):
                    return _combined.source_doc_id or ""
                return ""

            def _iter_pairwise_groups():
                if _result.source_doc_results:
                    for _source_doc_id, _source_result in _result.source_doc_results.items():
                        if getattr(_source_result, "pairwise_results", None):
                            yield _source_doc_id, _source_result.pairwise_results, "pre_combine"
                        if getattr(_source_result, "post_combine_eval_results", None):
                            yield _source_doc_id, _source_result.post_combine_eval_results, "post_combine"
                    return
                if _result.pairwise_results:
                    yield None, _result.pairwise_results, "pre_combine"
                if getattr(_result, "post_combine_eval_results", None):
                    yield None, _result.post_combine_eval_results, "post_combine"

            if _result.winner_doc_id:
                await _crr.set_metadata(run_id, "winner", _result.winner_doc_id)

            for _source_doc_id, _summary, _comparison_type in _iter_pairwise_groups():
                for _pr in (getattr(_summary, "results", None) or []):
                    await _crr.insert_pairwise_result(
                        run_id=run_id,
                        source_doc_id=(
                            _source_doc_id
                            or _source_doc_id_for(getattr(_pr, "doc_id_1", None))
                            or _source_doc_id_for(getattr(_pr, "doc_id_2", None))
                        ),
                        doc_id_a=_pr.doc_id_1,
                        doc_id_b=_pr.doc_id_2,
                        winner_doc_id=_pr.winner_doc_id,
                        judge_model=_pr.model,
                        trial=_pr.trial,
                        reason=_pr.reason,
                        comparison_type=_comparison_type,
                        compared_at=(
                            getattr(_pr, "completed_at", None)
                            or getattr(_pr, "timestamp", None)
                            or _persisted_at
                        ),
                    )

            for _cd in (_result.combined_docs or []):
                await _crr.insert_combined_doc(
                    run_id=run_id,
                    doc_id=_cd.doc_id,
                    source_doc_id=_cd.source_doc_id or "",
                    combine_model=_cd.model,
                    combine_strategy=(
                        _cd.generator.value
                        if hasattr(_cd.generator, "value")
                        else str(_cd.generator)
                    ),
                    input_doc_ids=[
                        _generated.doc_id
                        for _generated in (_result.generated_docs or [])
                        if _generated.source_doc_id == _cd.source_doc_id
                    ],
                    duration_seconds=getattr(_cd, "duration_seconds", None),
                    started_at=getattr(_cd, "started_at", None),
                    completed_at=getattr(_cd, "completed_at", None) or _persisted_at,
                )

            if _result.source_doc_results:
                for _source_doc_id, _source_result in _result.source_doc_results.items():
                    _status = getattr(_source_result, "status", "completed")
                    _status_value = _status.value if hasattr(_status, "value") else str(_status)
                    _errors = list(getattr(_source_result, "errors", None) or [])
                    await _crr.upsert_source_doc_status(
                        run_id=run_id,
                        source_doc_id=_source_doc_id,
                        source_doc_name=getattr(_source_result, "source_doc_name", None),
                        status=_status_value,
                        winner_doc_id=getattr(_source_result, "winner_doc_id", None),
                        error_message="; ".join(_errors) if _errors else None,
                        started_at=getattr(_source_result, "started_at", None),
                        completed_at=(
                            getattr(_source_result, "completed_at", None)
                            or _persisted_at
                        ),
                    )

        async def _persist_run_diagnostic_metadata(
            _result,
            *,
            _session=None,
        ) -> None:
            """Persist additive diagnostic metadata that should survive terminal states."""
            if _session is None:
                async with get_user_session_by_uuid(config.user_uuid) as _diagnostic_session:
                    await _persist_run_diagnostic_metadata(
                        _result,
                        _session=_diagnostic_session,
                    )
                return

            from app.infra.db.repositories.run_results import RunResultsRepository as _CRR

            _crr = _CRR(_session)
            _metadata_updates: dict[str, str] = {}

            if _result.fpf_stats:
                _metadata_updates["fpf_stats"] = json.dumps(_result.fpf_stats)

            _invariant_failures = list(getattr(_result, "locked_invariant_failures", None) or [])
            if _invariant_failures:
                _metadata_updates["invariant_failures"] = json.dumps(_invariant_failures)

            for _key, _value in _metadata_updates.items():
                await _crr.set_metadata(run_id, _key, _value)

            _stale_keys = [
                _key for _key in ("fpf_stats", "invariant_failures")
                if _key not in _metadata_updates
            ]
            if _stale_keys:
                await _crr.delete_metadata_keys(run_id, _stale_keys)

        def _build_completion_timeline(_result) -> list[dict]:
            _generation_events = []

            for _gen_doc in _result.generated_docs:
                _generation_events.append({
                    "doc_id": _gen_doc.doc_id,
                    "generator": (
                        _gen_doc.generator.value
                        if hasattr(_gen_doc.generator, "value")
                        else str(_gen_doc.generator)
                    ),
                    "model": _gen_doc.model,
                    "source_doc_id": _gen_doc.source_doc_id,
                    "iteration": _gen_doc.iteration,
                    "duration_seconds": _gen_doc.duration_seconds,
                    "status": "completed",
                    "started_at": (
                        _gen_doc.started_at.isoformat()
                        if getattr(_gen_doc, "started_at", None)
                        else None
                    ),
                    "completed_at": (
                        _gen_doc.completed_at.isoformat()
                        if getattr(_gen_doc, "completed_at", None)
                        else None
                    ),
                })
            _timeline_events = []
            if _result.started_at:
                _timeline_events.append({
                    "phase": "initialization",
                    "event_type": "start",
                    "description": "Run started",
                    "model": None,
                    "timestamp": _result.started_at.isoformat(),
                    "duration_seconds": None,
                    "success": True,
                    "details": None,
                })

            for _gen_event in _generation_events:
                _timeline_events.append({
                    "phase": "generation",
                    "event_type": "generation",
                    "description": f"Generated doc using {_gen_event['generator']}",
                    "model": _gen_event.get("model"),
                    "timestamp": _gen_event.get("started_at"),
                    "completed_at": _gen_event.get("completed_at"),
                    "duration_seconds": _gen_event.get("duration_seconds"),
                    "success": _gen_event.get("status") == "completed",
                    "details": {
                        "doc_id": _gen_event.get("doc_id"),
                        "source_doc_id": _gen_event.get("source_doc_id"),
                    },
                })

            if _result.pairwise_results and _result.pairwise_results.results:
                for _pw_result in _result.pairwise_results.results:
                    _timeline_events.append({
                        "phase": "pairwise",
                        "event_type": "pairwise_eval",
                        "description": (
                            f"Compared {_pw_result.doc_id_1[:15]}... vs {_pw_result.doc_id_2[:15]}..."
                        ),
                        "model": _pw_result.model,
                        "timestamp": (
                            _pw_result.started_at.isoformat()
                            if getattr(_pw_result, "started_at", None)
                            else None
                        ),
                        "completed_at": (
                            _pw_result.completed_at.isoformat()
                            if getattr(_pw_result, "completed_at", None)
                            else None
                        ),
                        "duration_seconds": getattr(_pw_result, "duration_seconds", None),
                        "success": True,
                        "details": {
                            "doc_id_1": _pw_result.doc_id_1,
                            "doc_id_2": _pw_result.doc_id_2,
                            "winner": _pw_result.winner_doc_id,
                            "trial": _pw_result.trial,
                        },
                    })

            for _combined_doc in (_result.combined_docs or []):
                _timeline_events.append({
                    "phase": "combination",
                    "event_type": "combine",
                    "description": f"Combined documents using {_combined_doc.model}",
                    "model": _combined_doc.model,
                    "timestamp": (
                        _combined_doc.started_at.isoformat()
                        if getattr(_combined_doc, "started_at", None)
                        else None
                    ),
                    "completed_at": (
                        _combined_doc.completed_at.isoformat()
                        if getattr(_combined_doc, "completed_at", None)
                        else None
                    ),
                    "duration_seconds": _combined_doc.duration_seconds,
                    "success": True,
                    "details": {"combined_doc_id": _combined_doc.doc_id},
                })

            if _result.completed_at:
                _timeline_events.append({
                    "phase": "completion",
                    "event_type": "complete",
                    "description": "Run completed successfully",
                    "model": None,
                    "timestamp": _result.completed_at.isoformat(),
                    "duration_seconds": _result.duration_seconds,
                    "success": True,
                    "details": None,
                })

            return _timeline_events

        def _timeline_event_occurred_at(_timeline_event: dict, *, _fallback: datetime) -> datetime:
            _raw_timestamp = _timeline_event.get("timestamp") or _timeline_event.get("completed_at")
            if isinstance(_raw_timestamp, datetime):
                return _raw_timestamp
            if isinstance(_raw_timestamp, str):
                try:
                    return datetime.fromisoformat(_raw_timestamp)
                except (ValueError, TypeError):
                    return _fallback
            return _fallback

        async def _persist_append_only_completion_artifacts(
            _result,
            *,
            _timeline_events: list[dict],
            _now_c: datetime,
            _session,
        ) -> None:
            from app.infra.db.repositories.run_results import RunResultsRepository as _CRR

            _crr = _CRR(_session)
            for _timeline_event in _timeline_events:
                _details = _timeline_event.get("details") or {}
                await _crr.insert_timeline_event(
                    run_id=run_id,
                    phase=_timeline_event.get("phase", "unknown"),
                    event_type=_timeline_event.get("event_type", "unknown"),
                    source_doc_id=_details.get("source_doc_id"),
                    doc_id=_details.get("doc_id"),
                    description=_timeline_event.get("description"),
                    model=_timeline_event.get("model"),
                    success=_timeline_event.get("success", True),
                    duration_seconds=_timeline_event.get("duration_seconds"),
                    occurred_at=_timeline_event_occurred_at(
                        _timeline_event,
                        _fallback=_now_c,
                    ),
                )


        async def _finalize_run_status(
            _result,
            *,
            _run_repo,
            _session,
        ) -> None:
            _error_message = "; ".join(_result.errors) if _result.errors else None
            if _result.status.value in ("completed", "completed_with_errors"):
                if _result.status.value == "completed_with_errors":
                    await _run_repo.complete_with_errors(
                        run_id,
                        error_message=_error_message,
                    )
                    logger.info("Run %s completed with errors", run_id)
                    await log_writer.event(
                        "apicostx",
                        "WARNING",
                        "run_complete",
                        (
                            f"Run {run_id} completed with errors: "
                            f"{_error_message or 'partial results saved'}"
                        ),
                    )
                else:
                    await _run_repo.complete(run_id)
                    logger.info("Run %s completed successfully", run_id)
                    await log_writer.event(
                        "apicostx",
                        "INFO",
                        "run_complete",
                        f"Run {run_id} completed: status=success, duration={_result.duration_seconds:.1f}s",
                    )
                return

            if executor._paused:
                await _run_repo.pause(run_id)
                logger.info("Run %s paused gracefully", run_id)
                await log_writer.event(
                    "apicostx",
                    "INFO",
                    "run_paused",
                    f"Run {run_id} paused gracefully",
                )
                return

            _stats_last_error = _result.fpf_stats.get("last_error") if _result.fpf_stats else None
            _final_error = _error_message or _stats_last_error or "Unknown error"
            await _run_repo.fail(run_id, error_message=_final_error)
            logger.error("Run %s failed: error_type=%s", run_id[:8], type(_final_error).__name__)
            await log_writer.event(
                "apicostx",
                "ERROR",
                "run_failed",
                f"Run {run_id} failed: error_type={type(_final_error).__name__}",
            )
            await log_writer.detail(
                "apicostx",
                "ERROR",
                "Run failure details",
                payload={"error": str(_final_error)},
            )

        async def _apply_persisted_source_doc_status_overrides(
            _result,
            *,
            _session,
        ) -> None:
            if _result.status.value not in ("completed", "completed_with_errors"):
                return

            try:
                from app.infra.db.repositories.run_results import RunResultsRepository as _RunResultsRepo

                _results_repo = _RunResultsRepo(_session)
                _get_statuses = getattr(_results_repo, "get_source_doc_statuses", None)
                if not callable(_get_statuses):
                    return
                _persisted_rows = list(await _get_statuses(run_id))
            except Exception as _status_err:
                logger.warning(
                    "[FINALIZATION] Failed to cross-check persisted source-doc status for run %s: %s",
                    run_id[:8],
                    _status_err,
                )
                return

            _durable_degraded_message = None
            for _row in _persisted_rows:
                _row_status = str(getattr(_row, "status", "") or "")
                _row_error = str(getattr(_row, "error_message", "") or "")
                _source_doc_id = str(getattr(_row, "source_doc_id", "") or "")
                _is_degraded = _row_status in (
                    "completed_with_errors",
                    "failed",
                    "cancelled",
                ) or bool(_row_error)
                if not _is_degraded:
                    continue

                if _durable_degraded_message is None:
                    _durable_degraded_message = _row_error or "Persisted source-document status shows partial results"

                if _result.source_doc_results and _source_doc_id in _result.source_doc_results:
                    _source_result = _result.source_doc_results[_source_doc_id]
                    if _row_status == "failed":
                        _source_result.status = RunPhase.FAILED
                    elif _row_status == "cancelled":
                        _source_result.status = RunPhase.CANCELLED
                    else:
                        _source_result.status = RunPhase.COMPLETED_WITH_ERRORS
                    _existing_errors = list(getattr(_source_result, "errors", None) or [])
                    if _row_error and _row_error not in _existing_errors:
                        _existing_errors.append(_row_error)
                        _source_result.errors = _existing_errors

            if _durable_degraded_message and _result.status == RunPhase.COMPLETED:
                _result.status = RunPhase.COMPLETED_WITH_ERRORS
                if _durable_degraded_message not in (_result.errors or []):
                    _result.errors.append(_durable_degraded_message)

        # Update run in DB
        async with get_user_session_by_uuid(config.user_uuid) as session:
            run_repo = RunRepository(session, user_uuid=config.user_uuid)
            current_run = await run_repo.get_by_id(run_id)

            if current_run and getattr(current_run, "status", None) == RunStatus.CANCELLED:
                try:
                    await _persist_run_diagnostic_metadata(result, _session=session)
                    await _persist_stable_result_artifacts(result)
                except Exception as _cancel_write_err:
                    logger.warning(
                        "[COMPLETION] Stable cancelled-run writes failed for run %s: %s",
                        run_id[:8],
                        _cancel_write_err,
                    )
                await run_repo.update(
                    run_id,
                    completed_at=getattr(current_run, "completed_at", None) or datetime.utcnow(),
                    pause_requested=0,
                )
                logger.info("Run %s already cancelled; preserving cancelled status", run_id[:8])
                await log_writer.event("apicostx", "INFO", "run_cancelled",
                                       f"Run {run_id} cancelled: preserving cancelled status")
                _pending_db_key_evict_reason = "run_cancelled"
                return

            await _apply_persisted_source_doc_status_overrides(
                result,
                _session=session,
            )

            try:
                await _persist_run_diagnostic_metadata(result, _session=session)
            except Exception as _diagnostic_write_err:
                logger.warning(
                    "[COMPLETION] Diagnostic metadata persistence failed for run %s: %s",
                    run_id[:8],
                    _diagnostic_write_err,
                )

            if result.status.value in ("completed", "completed_with_errors"):
                timeline_events = _build_completion_timeline(result)
                logger.info("[STATS] Persisting stats to database for run %s", run_id)
                await log_writer.event(
                    "apicostx",
                    "INFO",
                    "stats_persist",
                    f"Persisting run stats for {run_id[:8]}",
                )
                await log_writer.detail(
                    "apicostx",
                    "DEBUG",
                    "Full FPF stats for run",
                    payload=result.fpf_stats,
                )

                try:
                    _now_c = datetime.utcnow()

                    async def _write_completion_artifacts_once() -> None:
                        await write_finalization_marker(
                            session,
                            run_id,
                            phase="writing_stable_artifacts",
                            terminal_status=result.status.value,
                        )
                        await _persist_stable_result_artifacts(
                            result,
                            _now_c=_now_c,
                            _session=session,
                        )
                        await write_finalization_marker(
                            session,
                            run_id,
                            phase="writing_append_only_artifacts",
                            terminal_status=result.status.value,
                        )
                        await _persist_append_only_completion_artifacts(
                            result,
                            _timeline_events=timeline_events,
                            _now_c=_now_c,
                            _session=session,
                        )
                        await write_finalization_marker(
                            session,
                            run_id,
                            phase="completion_artifacts_persisted",
                            terminal_status=result.status.value,
                        )

                    await _retry_sqlite_locked_write(
                        "completion artifact persistence",
                        _write_completion_artifacts_once,
                        session=session,
                    )
                    logger.info(
                        "[COMPLETION] Normalized completion writes done for run %s",
                        run_id[:8],
                    )
                except Exception as _completion_write_err:
                    _completion_error_type = type(_completion_write_err).__name__
                    _completion_error_message = (
                        f"Finalization artifacts failed: {_completion_error_type}"
                    )
                    logger.error(
                        "[COMPLETION] Normalized completion writes failed for run %s: %s",
                        run_id[:8],
                        _completion_error_type,
                        exc_info=True,
                    )
                    if result.status == RunPhase.COMPLETED:
                        result.status = RunPhase.COMPLETED_WITH_ERRORS
                    _completion_errors = list(getattr(result, "errors", None) or [])
                    if _completion_error_message not in _completion_errors:
                        _completion_errors.append(_completion_error_message)
                        result.errors = _completion_errors
                    try:
                        async with get_user_session_by_uuid(config.user_uuid) as _marker_session:
                            await write_finalization_marker(
                                _marker_session,
                                run_id,
                                phase="completion_artifacts_failed",
                                terminal_status=result.status.value,
                                error=_completion_error_message,
                            )
                    except Exception as _marker_err:
                        logger.error(
                            "[COMPLETION] Could not persist completion failure marker for run %s: %s",
                            run_id[:8],
                            type(_marker_err).__name__,
                            exc_info=True,
                        )

            if result.status.value in ("completed", "completed_with_errors"):
                await _retry_sqlite_locked_write(
                    "terminal-status finalization marker",
                    lambda: write_finalization_marker(
                        session,
                        run_id,
                        phase="applying_terminal_status",
                        terminal_status=result.status.value,
                    ),
                    session=session,
                )

            await _retry_sqlite_locked_write(
                "run terminal status update",
                lambda: _finalize_run_status(
                    result,
                    _run_repo=run_repo,
                    _session=session,
                ),
                session=session,
            )

            if result.status.value in ("completed", "completed_with_errors"):
                evict_run_detail(user_uuid=config.user_uuid, run_id=run_id)
                await _retry_sqlite_locked_write(
                    "clear finalization markers",
                    lambda: clear_finalization_markers(session, run_id),
                    session=session,
                )

            _pending_db_key_evict_reason = f"run_{result.status.value}"

    except Exception as e:
        _pending_db_key_evict_reason = "run_exception"
        import traceback as _tb_mod
        logger.error("Unexpected error executing run %s: %s", run_id[:8], type(e).__name__)
        await log_writer.event("apicostx", "ERROR", "run_crash",
                               f"Run {run_id} unexpected error: type={type(e).__name__}")
        await log_writer.detail("apicostx", "ERROR", "Run crash traceback",
                                payload={"traceback": _tb_mod.format_exc()})
        async with get_user_session_by_uuid(config.user_uuid) as session:
            run_repo = RunRepository(session, user_uuid=config.user_uuid)
            await run_repo.fail(run_id, error_message="Run execution failed")
    finally:
        try:
            popped = _active_executors.pop(run_id, None)
            logger.debug(f"Executor cleanup for run {run_id}; popped={bool(popped)}")
        except Exception:
            logger.exception("Failed to pop active executor")
        if run_logger:
            for handler in run_logger.handlers[:]:
                run_logger.removeHandler(handler)
                try:
                    handler.flush()
                    handler.close()
                except Exception:
                    logger.exception("Failed to close run logger handler")
        # Flush sidecar log writer after detaching handlers so no new emits race shutdown.
        try:
            await log_writer.close()
        except Exception:
            logger.debug("Failed to close log_writer", exc_info=True)
        if _pending_db_key_evict_reason:
            logger.debug("Run cleanup complete for %s (%s)", run_id, _pending_db_key_evict_reason)


@router.post("/runs/{run_id}/start")
async def start_run(
    run_id: str,
    background_tasks: BackgroundTasks,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db)
) -> dict:
    """
    Start executing a run.
    """
    repo = RunRepository(db, user_uuid=user['uuid'])
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    if run.status != RunStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Can only start PENDING runs, current status: {run.status}"
        )

    if not run.preset_id:
        raise HTTPException(status_code=400, detail="Cannot start run: run was not created from a preset")

    # NOTE: repo.start(run_id) is called AFTER all validation succeeds (before background task)

    try:
        executor_config, document_contents = await _build_executor_config(
            run_id=run_id, run=run, user=user, db=db
        )
    except PresetLaunchValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"message": "Run cannot start with the current preset/config state", "errors": exc.errors},
        )

    # Set status to RUNNING only after all validation succeeds
    await repo.start(run_id)

    # Initialize source_doc_results for immediate web GUI display
    # This MUST happen before background task so web GUI sees collapsible sections immediately
    source_doc_results_init = {}
    for doc_id in document_contents.keys():
        source_doc_results_init[doc_id] = {
            "source_doc_id": doc_id,
            "source_doc_name": doc_id,
            "status": "pending",
            "generated_docs": [],
            "single_eval_results": {},
            "pairwise_results": None,
            "winner_doc_id": None,
            "combined_doc": None,
            "timeline_events": [],
            "errors": [],
            "duration_seconds": 0.0,
        }

    logger.info(f"[INIT] Pre-initialized source_doc_results with {len(document_contents)} input documents")

    # Write pending source_doc_status rows using a dedicated session that commits
    # immediately — the incoming GET from the web GUI races the /start response,
    # so the rows MUST be visible before this handler returns.
    from app.infra.db.repositories.run_results import RunResultsRepository as _InitRR
    from app.infra.db.session import get_user_session_by_uuid
    async with get_user_session_by_uuid(executor_config.user_uuid) as _init_session:
        _init_rr = _InitRR(_init_session)
        for _init_doc_id, _init_doc_name in executor_config.document_names.items():
            await _init_rr.upsert_source_doc_status(
                run_id=run_id,
                source_doc_id=_init_doc_id,
                source_doc_name=_init_doc_name,
                status="pending",
            )
            logger.info("[INIT] Wrote pending source_doc_status for doc_id=%s", _init_doc_id[:8])
        # commit is handled by the async context manager on exit

    background_tasks.add_task(execute_run_background, run_id, executor_config)

    return {"status": "started", "run_id": run_id}


async def _build_executor_config(
    run_id: str,
    run,
    user: Dict[str, Any],
    db: AsyncSession,
) -> tuple:
    return await build_executor_config_from_run_snapshot(
        run_id=run_id,
        run_config=run.config or {},
        preset=None,
        user=user,
        db=db,
    )



@router.post("/runs/{run_id}/pause")
async def pause_run(
    run_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db)
) -> dict:
    """
    Pause a running run.

    Signals the active executor to drain-and-hold: tasks already in flight
    will complete, but tasks waiting on the concurrency semaphore will see
    the pause flag and return without doing work.

    The DB status is set to PAUSED by the background task once all pipelines
    drain.  We set pause_requested=1 here so the UI can show "pausing" while
    the drain is in progress.
    """
    repo = RunRepository(db, user_uuid=user['uuid'])
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    if run.status != RunStatus.RUNNING:
        raise HTTPException(
            status_code=400,
            detail=f"Can only pause RUNNING runs, current status: {run.status}"
        )

    # Signal the executor (if still in-process)
    executor = _active_executors.get(run_id)
    if executor:
        logger.info(f"Signaling pause for run {run_id}")
        executor.pause()
    else:
        # No active executor - update DB status directly
        logger.info(f"No active executor for run {run_id}; setting PAUSED in DB directly")
        await repo.update(run_id, status=RunStatus.PAUSED)
        return {"status": "paused", "run_id": run_id}

    # Record pause_requested=1 in DB so the UI can show "pausing…" state
    await repo.set_pause_requested(run_id, 1)
    return {"status": "pausing", "run_id": run_id}


@router.post("/runs/{run_id}/resume")
async def resume_run(
    run_id: str,
    background_tasks: BackgroundTasks,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db)
) -> dict:
    """
    Resume a paused or failed run.

    Steps:
    1. Validate run is PAUSED or FAILED.
    2. Reset any stale RUNNING tasks in the task table back to PENDING
       (tasks that were in-flight when the process died).
    3. Rebuild the RunConfig from the persisted run.config (same as start_run).
    4. Call repo.resume() to set status=RUNNING, clear pause_requested,
       and bump resume_count.
    5. Launch execute_run_background as a new background task.
    """
    repo = RunRepository(db, user_uuid=user['uuid'])
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    resume_info = await build_run_resume_info(
        db=db,
        run=run,
        active_executor_present=bool(_active_executors.get(run_id)),
    )
    if not resume_info["resumable"]:
        raise HTTPException(
            status_code=400,
            detail={
                "message": resume_info["reason"],
                "resume_info": resume_info,
            },
        )

    # Reset any stale RUNNING tasks (tasks that were in-flight when the executor died)
    task_repo = TaskRepository(db)
    stale_count = await task_repo.reset_stale_running(run_id)
    if stale_count:
        logger.info(f"Resume run {run_id}: reset {stale_count} stale RUNNING tasks to PENDING")

    # Rebuild executor config from persisted run.config (same logic as start_run)
    try:
        executor_config, document_contents = await _build_executor_config(
            run_id=run_id, run=run, user=user, db=db
        )
    except PresetLaunchValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"message": "Run cannot resume with the current preset/config state", "errors": exc.errors},
        )

    # Build completed_generation_cache so the executor skips already-finished tasks
    try:
        from pathlib import Path as _P
        completed_gen_tasks = await task_repo.get_tasks_by_phase(run_id, "generation")
        completed_cache: Dict[str, dict] = {}
        for _t in completed_gen_tasks:
            if _t.status != "completed" or not _t.output_ref:
                continue
            _key = f"{_t.document_id}:{_t.model_name}:{_t.iteration}"
            _doc_id = _P(_t.output_ref).stem  # e.g. "generated/abc123.md" → "abc123"
            completed_cache[_key] = {
                "doc_id": _doc_id,
                "output_ref": _t.output_ref,
                "generator": _t.generator or "",
            }
        executor_config.completed_generation_cache = completed_cache
        if completed_cache:
            logger.info(
                f"Resume run {run_id}: pre-loaded {len(completed_cache)} completed"
                f" generation tasks into cache"
            )
    except Exception as _cache_err:
        logger.warning(
            f"Resume run {run_id}: failed to build completed_generation_cache"
            f" (non-fatal): {_cache_err}"
        )

    # Transition the run to RUNNING (also clears pause_requested and bumps resume_count)
    resumed = await repo.resume(
        run_id,
        allow_interrupted=(resume_info.get("resume_mode") == "interrupted"),
        allow_terminal_incomplete=(resume_info.get("resume_mode") == "terminal_incomplete"),
    )
    if not resumed:
        raise HTTPException(status_code=409, detail="Failed to resume run — status may have changed")

    logger.info(
        "Resuming run %s (mode=%s, resume_count=%s)",
        run_id,
        resume_info.get("resume_mode"),
        resumed.resume_count,
    )

    background_tasks.add_task(execute_run_background, run_id, executor_config)

    return {
        "status": "running",
        "run_id": run_id,
        "resume_count": resumed.resume_count,
        "resume_mode": resume_info.get("resume_mode"),
    }


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db)
) -> dict:
    """
    Cancel a running or paused run.
    """
    repo = RunRepository(db, user_uuid=user['uuid'])
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    if run.status not in (RunStatus.RUNNING, RunStatus.PAUSED, RunStatus.PENDING):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel run in status: {run.status}"
        )

    executor = _active_executors.get(run_id)
    if executor:
        logger.info(f"Signaling cancellation for run {run_id}")
        executor.cancel()
    else:
        logger.info(f"No active executor found for run {run_id}, just updating status")

    await repo.update(
        run_id,
        status=RunStatus.CANCELLED,
        completed_at=datetime.utcnow(),
        pause_requested=0,
    )
    return {"status": "cancelled", "run_id": run_id}

@router.get("/runs/{run_id}/checkpoint")
async def get_run_checkpoint(
    run_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db)
) -> dict:
    """
    Return a task-level checkpoint summary for a run.

    Useful for:
    - Deciding whether to resume or restart after a crash
    - Monitoring mid-run progress at task granularity
    - Forensic analysis of which tasks completed/failed

    Returns per-phase counts (total/completed/failed/pending/running/skipped/cancelled)
    plus a flat task list with per-task status and timing.
    """
    repo = RunRepository(db, user_uuid=user['uuid'])
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    task_repo = TaskRepository(db)
    summary = await task_repo.get_checkpoint_summary(run_id)

    return {
        "run_id": run_id,
        "run_status": run.status,
        "pause_requested": getattr(run, "pause_requested", 0),
        "resume_count": getattr(run, "resume_count", 0),
        **summary,
    }


@router.get("/runs/{run_id}/resume-info", response_model=RunResumeInfo)
async def get_run_resume_info(
    run_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> RunResumeInfo:
    """Return the canonical resumability decision for a run."""
    repo = RunRepository(db, user_uuid=user["uuid"])
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    return RunResumeInfo(
        **(
            await build_run_resume_info(
                db=db,
                run=run,
                active_executor_present=bool(_active_executors.get(run_id)),
            )
        )
    )


@router.post("/runs/{run_id}/tasks/{task_key:path}/skip")
async def skip_task(
    run_id: str,
    task_key: str,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db)
) -> dict:
    """
    Mark a specific generation task as completed/skipped so it won't be re-run
    on the next resume.

    task_key format: "{source_doc_id}:{model}:{iteration}"

    Use this to manually advance past a stuck or undesired task without running
    full generation.  The task is recorded as completed with output_ref="skipped"
    so the executor will load it from cache (and find no file → will re-generate
    unless a real output file also exists).

    More commonly used to record tasks that succeeded via out-of-band means.
    """
    repo = RunRepository(db, user_uuid=user['uuid'])
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    parts = task_key.rsplit(":", 2)
    if len(parts) != 3:
        raise HTTPException(
            status_code=400,
            detail="task_key must be '{source_doc_id}:{model}:{iteration}'"
        )
    source_doc_id, model, iteration_str = parts
    try:
        iteration = int(iteration_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="iteration must be an integer")

    task_repo = TaskRepository(db)
    existing = await task_repo.find_completed_generation_task(
        run_id, source_doc_id, model, iteration
    )
    if existing:
        return {
            "status": "already_completed",
            "run_id": run_id,
            "task_key": task_key,
            "task_id": existing.id,
        }

    gen_task = await task_repo.create_generation_task(
        run_id=run_id,
        source_doc_id=source_doc_id,
        model_name=model,
        generator="skipped",
        iteration=iteration,
    )
    await task_repo.complete_with_output(gen_task.id, output_ref="skipped")

    logger.info(f"Manually skipped task {task_key} for run {run_id}")
    return {"status": "skipped", "run_id": run_id, "task_key": task_key, "task_id": gen_task.id}
