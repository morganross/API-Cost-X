"""
Helpers for durable run-finalization markers and stale-run reconciliation.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
import inspect
from typing import Any, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models.run import Run, RunStatus
from app.infra.db.repositories.run import RunRepository
from app.infra.db.repositories.run_results import RunResultsRepository
from app.infra.db.repositories.task import TaskRepository

logger = logging.getLogger(__name__)

FINALIZATION_PHASE_KEY = "finalization_phase"
TERMINAL_RESULT_STATUS_KEY = "terminal_result_status"
FINALIZATION_STARTED_AT_KEY = "finalization_started_at"
FINALIZATION_UPDATED_AT_KEY = "finalization_updated_at"
FINALIZATION_ERROR_KEY = "finalization_error"
REPAIR_CANONICAL_OUTCOME_KEY = "repair_canonical_outcome"
REPAIR_SOURCE_KEY = "repair_source"
REPAIR_UPDATED_AT_KEY = "repair_updated_at"

FINALIZATION_METADATA_KEYS = (
    FINALIZATION_PHASE_KEY,
    TERMINAL_RESULT_STATUS_KEY,
    FINALIZATION_STARTED_AT_KEY,
    FINALIZATION_UPDATED_AT_KEY,
    FINALIZATION_ERROR_KEY,
)
REPAIR_METADATA_KEYS = (
    REPAIR_CANONICAL_OUTCOME_KEY,
    REPAIR_SOURCE_KEY,
    REPAIR_UPDATED_AT_KEY,
)

CANONICAL_OUTCOME_COMPLETED = "completed"
CANONICAL_OUTCOME_COMPLETED_WITH_ERRORS = "completed_with_errors"
CANONICAL_OUTCOME_FAILED_INTERRUPTED = "failed_interrupted"
CANONICAL_OUTCOME_NEEDS_REPAIR = "needs_repair"

REPAIR_SOURCE_FINALIZATION_MARKER = "finalization_marker"
REPAIR_SOURCE_TERMINAL_ROW_STATE = "terminal_row_state"
REPAIR_SOURCE_TERMINAL_TASK_STATE = "terminal_task_state"
REPAIR_SOURCE_STALE_PRE_RESTART = "stale_pre_restart"
REPAIR_SOURCE_STARTUP_ORPHAN_RECOVERY = "startup_orphan_recovery"
REPAIR_SOURCE_UNRESOLVED = "unresolved"


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _empty_repair_outcomes() -> dict[str, int]:
    return {
        CANONICAL_OUTCOME_COMPLETED: 0,
        CANONICAL_OUTCOME_COMPLETED_WITH_ERRORS: 0,
        CANONICAL_OUTCOME_FAILED_INTERRUPTED: 0,
        CANONICAL_OUTCOME_NEEDS_REPAIR: 0,
    }


def _increment_repair_outcome(summary: dict[str, object], canonical_outcome: str) -> None:
    counts = summary.get("repair_outcomes")
    if not isinstance(counts, dict):
        counts = _empty_repair_outcomes()
        summary["repair_outcomes"] = counts
    counts[canonical_outcome] = int(counts.get(canonical_outcome, 0)) + 1


def _canonical_outcome_for_status(status: str) -> str:
    if status == RunStatus.COMPLETED_WITH_ERRORS.value:
        return CANONICAL_OUTCOME_COMPLETED_WITH_ERRORS
    if status == RunStatus.COMPLETED.value:
        return CANONICAL_OUTCOME_COMPLETED
    if status in (RunStatus.FAILED.value, RunStatus.CANCELLED.value):
        return CANONICAL_OUTCOME_FAILED_INTERRUPTED
    return CANONICAL_OUTCOME_NEEDS_REPAIR


def _legacy_action_for_repair_source(repair_source: str, canonical_outcome: str) -> str:
    if canonical_outcome == CANONICAL_OUTCOME_FAILED_INTERRUPTED:
        return "marked_interrupted"
    if repair_source == REPAIR_SOURCE_TERMINAL_TASK_STATE:
        return "reconciled_task_terminal_state"
    if canonical_outcome == CANONICAL_OUTCOME_NEEDS_REPAIR:
        return "flagged_needs_repair"
    return "reconciled_terminal_status"


async def _load_source_doc_status_signal(
    session: AsyncSession,
    run_id: str,
) -> tuple[bool, bool, bool]:
    try:
        repo = RunResultsRepository(session)
        get_statuses = getattr(repo, "get_source_doc_statuses", None)
        if not callable(get_statuses):
            return False, False, False
        rows = list(await get_statuses(run_id))
    except Exception:
        return False, False, False

    if not rows:
        return False, False, False

    terminal_statuses = {
        RunStatus.COMPLETED.value,
        RunStatus.COMPLETED_WITH_ERRORS.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
    }
    has_nonterminal = any(str(getattr(row, "status", "") or "") not in terminal_statuses for row in rows)
    has_degraded = any(
        str(getattr(row, "status", "") or "") in (
            RunStatus.COMPLETED_WITH_ERRORS.value,
            RunStatus.FAILED.value,
            RunStatus.CANCELLED.value,
        )
        or bool(getattr(row, "error_message", None))
        for row in rows
    )
    return True, has_nonterminal, has_degraded


def _make_repair_detail(
    run: Run,
    *,
    canonical_outcome: str,
    repair_source: str,
) -> dict[str, str]:
    return {
        "run_id": run.id,
        "title": run.title or run.id[:8],
        "action": _legacy_action_for_repair_source(repair_source, canonical_outcome),
        "outcome": canonical_outcome,
        "canonical_outcome": canonical_outcome,
        "repair_source": repair_source,
    }


async def _commit_if_supported(session: AsyncSession) -> None:
    commit = getattr(session, "commit", None)
    if commit is None:
        return
    result = commit()
    if inspect.isawaitable(result):
        await result


async def write_repair_metadata(
    session: AsyncSession,
    run_id: str,
    *,
    canonical_outcome: str,
    repair_source: str,
) -> None:
    repo = RunResultsRepository(session)
    now_iso = datetime.utcnow().isoformat()
    await repo.set_metadata(run_id, REPAIR_CANONICAL_OUTCOME_KEY, canonical_outcome)
    await repo.set_metadata(run_id, REPAIR_SOURCE_KEY, repair_source)
    await repo.set_metadata(run_id, REPAIR_UPDATED_AT_KEY, now_iso)
    await _commit_if_supported(session)


async def write_finalization_marker(
    session: AsyncSession,
    run_id: str,
    *,
    phase: str,
    terminal_status: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    repo = RunResultsRepository(session)
    now_iso = datetime.utcnow().isoformat()

    await repo.set_metadata(run_id, FINALIZATION_PHASE_KEY, phase)
    await repo.set_metadata(run_id, FINALIZATION_UPDATED_AT_KEY, now_iso)

    get_metadata_value = getattr(repo, "get_metadata_value", None)
    existing_started_at = None
    if callable(get_metadata_value):
        try:
            existing_started_at = await get_metadata_value(run_id, FINALIZATION_STARTED_AT_KEY)
        except Exception:
            existing_started_at = None
    if not existing_started_at:
        await repo.set_metadata(run_id, FINALIZATION_STARTED_AT_KEY, now_iso)

    if terminal_status:
        await repo.set_metadata(run_id, TERMINAL_RESULT_STATUS_KEY, terminal_status)

    if error:
        await repo.set_metadata(run_id, FINALIZATION_ERROR_KEY, error)

    await _commit_if_supported(session)


async def clear_finalization_markers(session: AsyncSession, run_id: str) -> None:
    repo = RunResultsRepository(session)
    delete_metadata_keys = getattr(repo, "delete_metadata_keys", None)
    if callable(delete_metadata_keys):
        await delete_metadata_keys(run_id, FINALIZATION_METADATA_KEYS)
    await _commit_if_supported(session)


async def reconcile_interrupted_finalization(
    session: AsyncSession,
    run_repo: RunRepository,
    run: Run,
) -> Tuple[Run, bool]:
    repo = RunResultsRepository(session)
    metadata = await repo.get_metadata(run.id)
    phase = metadata.get(FINALIZATION_PHASE_KEY) or ""
    terminal_status = metadata.get(TERMINAL_RESULT_STATUS_KEY) or ""

    if run.status in (
        RunStatus.COMPLETED.value,
        RunStatus.COMPLETED_WITH_ERRORS.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
    ):
        if phase or terminal_status:
            await clear_finalization_markers(session, run.id)
        return run, False

    if terminal_status not in (
        RunStatus.COMPLETED.value,
        RunStatus.COMPLETED_WITH_ERRORS.value,
    ):
        return run, False

    if terminal_status == RunStatus.COMPLETED_WITH_ERRORS.value:
        repaired = await run_repo.complete_with_errors(
            run.id,
            error_message=run.error_message or "Recovered from interrupted finalization",
        )
    else:
        repaired = await run_repo.complete(run.id)

    if repaired is None:
        return run, False

    logger.warning(
        "[FINALIZATION] Reconciled interrupted finalization for run %s via metadata markers (phase=%s, terminal_status=%s)",
        run.id[:8],
        phase or "?",
        terminal_status,
    )
    await clear_finalization_markers(session, run.id)
    await write_repair_metadata(
        session,
        run.id,
        canonical_outcome=_canonical_outcome_for_status(getattr(repaired, "status", terminal_status)),
        repair_source=REPAIR_SOURCE_FINALIZATION_MARKER,
    )
    return repaired, True


async def reconcile_terminal_row_state(
    run_repo: RunRepository,
    run: Run,
) -> Tuple[Run, bool]:
    """
    Repair runs whose durable row already looks terminal even though status is
    still stuck in an active value.

    This catches interrupted status-write cases where `completed_at` made it to
    the row but the final status value did not.
    """
    if run.status in (
        RunStatus.COMPLETED.value,
        RunStatus.COMPLETED_WITH_ERRORS.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
    ):
        return run, False

    if getattr(run, "completed_at", None) is None:
        return run, False

    source_doc_rows_present, source_doc_has_nonterminal, source_doc_has_degraded = await _load_source_doc_status_signal(
        run_repo.session,
        run.id,
    )
    if source_doc_rows_present and source_doc_has_nonterminal:
        return run, False

    if source_doc_has_degraded or (run.failed_tasks or 0) > 0 or bool(run.error_message):
        repaired = await run_repo.complete_with_errors(
            run.id,
            error_message=run.error_message or "Recovered terminal run state after interrupted status write",
        )
        repaired_status = RunStatus.COMPLETED_WITH_ERRORS.value
    else:
        repaired = await run_repo.complete(run.id)
        repaired_status = RunStatus.COMPLETED.value

    if repaired is None:
        return run, False

    logger.warning(
        "[FINALIZATION] Reconciled stale active run %s via terminal row state (completed_at=%s, repaired_status=%s)",
        run.id[:8],
        getattr(run, "completed_at", None),
        repaired_status,
    )
    await write_repair_metadata(
        run_repo.session,
        run.id,
        canonical_outcome=_canonical_outcome_for_status(getattr(repaired, "status", repaired_status)),
        repair_source=REPAIR_SOURCE_TERMINAL_ROW_STATE,
    )
    return repaired, True


async def reconcile_terminal_task_state(
    session: AsyncSession,
    run_repo: RunRepository,
    run: Run,
) -> Tuple[Run, bool]:
    """
    Repair active-looking runs whose task rows are already fully terminal.

    This catches cases where the run row never made it to a terminal status,
    but all task rows for the run are already completed/failed/cancelled.
    """
    if run.status in (
        RunStatus.COMPLETED.value,
        RunStatus.COMPLETED_WITH_ERRORS.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
    ):
        return run, False

    task_repo = TaskRepository(session)
    counts = await task_repo.get_status_counts_for_run(run.id)
    if not counts:
        return run, False

    pending = counts.get("pending", 0)
    running = counts.get("running", 0)
    completed = counts.get("completed", 0)
    failed = counts.get("failed", 0)
    cancelled = counts.get("cancelled", 0)

    total = pending + running + completed + failed + cancelled
    terminal = completed + failed + cancelled
    if total == 0 or terminal != total or pending > 0 or running > 0:
        return run, False

    expected_total = max(int(getattr(run, "total_tasks", 0) or 0), 0)
    durable_counter_total = max(
        int(getattr(run, "completed_tasks", 0) or 0) + int(getattr(run, "failed_tasks", 0) or 0),
        0,
    )
    if expected_total > 0 and total < expected_total:
        return run, False
    if durable_counter_total > 0 and total < durable_counter_total:
        return run, False
    if getattr(run, "completed_at", None) is None and expected_total <= 0 and durable_counter_total <= 0:
        return run, False

    source_doc_rows_present, source_doc_has_nonterminal, source_doc_has_degraded = await _load_source_doc_status_signal(
        session,
        run.id,
    )
    if source_doc_rows_present and source_doc_has_nonterminal:
        return run, False

    if source_doc_has_degraded or failed > 0 or cancelled > 0 or bool(run.error_message):
        repaired = await run_repo.complete_with_errors(
            run.id,
            error_message=run.error_message or "Recovered terminal run state from task rows",
        )
        repaired_status = RunStatus.COMPLETED_WITH_ERRORS.value
    else:
        repaired = await run_repo.complete(run.id)
        repaired_status = RunStatus.COMPLETED.value

    if repaired is None:
        return run, False

    logger.warning(
        "[FINALIZATION] Reconciled stale active run %s via task rows "
        "(completed=%d failed=%d cancelled=%d repaired_status=%s)",
        run.id[:8],
        completed,
        failed,
        cancelled,
        repaired_status,
    )
    await write_repair_metadata(
        session,
        run.id,
        canonical_outcome=_canonical_outcome_for_status(getattr(repaired, "status", repaired_status)),
        repair_source=REPAIR_SOURCE_TERMINAL_TASK_STATE,
    )
    return repaired, True


async def reconcile_cached_active_runs(
    *,
    fail_unreconciled: bool = False,
) -> dict[str, object]:
    """
    Reconcile active runs for local plain SQLite databases.
    """
    from app.auth.user_registry import get_all_user_uuids
    from app.api.routes.runs.execution import _active_executors
    from app.infra.db.session import get_user_session_by_uuid

    summary = {
        "users_total": 0,
        "active_runs_seen": 0,
        "reconciled": 0,
        "failed": 0,
        "user_errors": 0,
        "repair_outcomes": _empty_repair_outcomes(),
    }

    all_user_uuids = get_all_user_uuids()
    for user_uuid in all_user_uuids:
        summary["users_total"] += 1
        try:
            async with get_user_session_by_uuid(user_uuid) as session:
                run_repo = RunRepository(session, user_uuid=user_uuid)
                active_runs = await run_repo.get_active_runs()
                summary["active_runs_seen"] += len(active_runs)

                for run in active_runs:
                    if run.id in _active_executors:
                        continue

                    repaired_run, repaired = await reconcile_interrupted_finalization(
                        session,
                        run_repo,
                        run,
                    )
                    if repaired:
                        summary["reconciled"] += 1
                        _increment_repair_outcome(
                            summary,
                            _canonical_outcome_for_status(getattr(repaired_run, "status", "")),
                        )
                        continue

                    repaired_run, repaired = await reconcile_terminal_row_state(
                        run_repo,
                        run,
                    )
                    if repaired:
                        summary["reconciled"] += 1
                        _increment_repair_outcome(
                            summary,
                            _canonical_outcome_for_status(getattr(repaired_run, "status", "")),
                        )
                        continue

                    repaired_run, repaired = await reconcile_terminal_task_state(
                        session,
                        run_repo,
                        run,
                    )
                    if repaired:
                        summary["reconciled"] += 1
                        _increment_repair_outcome(
                            summary,
                            _canonical_outcome_for_status(getattr(repaired_run, "status", "")),
                        )
                        continue

                    if fail_unreconciled and run.status in (
                        RunStatus.RUNNING.value,
                        RunStatus.PENDING.value,
                    ):
                        logger.warning(
                            "Marking orphaned run %s (user %s) as failed",
                            run.id,
                            user_uuid,
                        )
                        failed_run = await run_repo.fail(
                            run.id,
                            error_message=(
                                "Run orphaned by server restart. "
                                "The server was restarted while this run was in progress."
                            ),
                        )
                        if failed_run is not None:
                            await write_repair_metadata(
                                session,
                                run.id,
                                canonical_outcome=CANONICAL_OUTCOME_FAILED_INTERRUPTED,
                                repair_source=REPAIR_SOURCE_STARTUP_ORPHAN_RECOVERY,
                            )
                        summary["failed"] += 1
                        _increment_repair_outcome(summary, CANONICAL_OUTCOME_FAILED_INTERRUPTED)
        except Exception as exc:
            summary["user_errors"] += 1
            logger.warning(
                "Failed to reconcile active runs for user %s: %s",
                user_uuid,
                exc,
            )

    return summary


async def reconcile_user_active_runs_after_key_restore(user_uuid: str) -> dict[str, object]:
    """
    Reconcile active runs for one user immediately after their DB key is restored.

    This is intended for the post-login/post-re-auth path. It attempts precise
    interrupted-finalization repair first, then downgrades clearly stale active
    runs that predate the current API service process and no longer have a live
    executor in memory.
    """
    from app.api.routes.internal_dashboard import _PROCESS_STARTED_AT
    from app.api.routes.runs.execution import _active_executors
    from app.infra.db.session import get_user_session_by_uuid
    from app.services.run_detail_cache import evict_run_detail

    process_started_at = _as_utc(_PROCESS_STARTED_AT)
    details: list[dict[str, str]] = []
    summary: dict[str, object] = {
        "user_uuid_suffix": user_uuid[-8:],
        "active_runs_seen": 0,
        "reconciled": 0,
        "failed_stale": 0,
        "left_running": 0,
        "repair_outcomes": _empty_repair_outcomes(),
        "process_started_at": process_started_at.isoformat() if process_started_at else "",
        "details": details,
    }

    async with get_user_session_by_uuid(user_uuid) as session:
        run_repo = RunRepository(session, user_uuid=user_uuid)
        active_runs = await run_repo.get_active_runs()
        summary["active_runs_seen"] = len(active_runs)

        for run in active_runs:
            if run.id in _active_executors:
                summary["left_running"] = int(summary["left_running"]) + 1
                continue

            repaired_run, repaired = await reconcile_interrupted_finalization(
                session,
                run_repo,
                run,
            )
            if repaired:
                evict_run_detail(user_uuid=user_uuid, run_id=run.id)
                summary["reconciled"] = int(summary["reconciled"]) + 1
                canonical_outcome = _canonical_outcome_for_status(getattr(repaired_run, "status", ""))
                _increment_repair_outcome(summary, canonical_outcome)
                details.append(
                    _make_repair_detail(
                        repaired_run,
                        canonical_outcome=canonical_outcome,
                        repair_source=REPAIR_SOURCE_FINALIZATION_MARKER,
                    )
                )
                continue

            repaired_run, repaired = await reconcile_terminal_row_state(
                run_repo,
                run,
            )
            if repaired:
                evict_run_detail(user_uuid=user_uuid, run_id=run.id)
                summary["reconciled"] = int(summary["reconciled"]) + 1
                canonical_outcome = _canonical_outcome_for_status(getattr(repaired_run, "status", ""))
                _increment_repair_outcome(summary, canonical_outcome)
                details.append(
                    _make_repair_detail(
                        repaired_run,
                        canonical_outcome=canonical_outcome,
                        repair_source=REPAIR_SOURCE_TERMINAL_ROW_STATE,
                    )
                )
                continue

            repaired_run, repaired = await reconcile_terminal_task_state(
                session,
                run_repo,
                run,
            )
            if repaired:
                evict_run_detail(user_uuid=user_uuid, run_id=run.id)
                summary["reconciled"] = int(summary["reconciled"]) + 1
                canonical_outcome = _canonical_outcome_for_status(getattr(repaired_run, "status", ""))
                _increment_repair_outcome(summary, canonical_outcome)
                details.append(
                    _make_repair_detail(
                        repaired_run,
                        canonical_outcome=canonical_outcome,
                        repair_source=REPAIR_SOURCE_TERMINAL_TASK_STATE,
                    )
                )
                continue

            reference_time = _as_utc(getattr(run, "started_at", None) or getattr(run, "created_at", None))
            if process_started_at and reference_time and reference_time < process_started_at:
                logger.warning(
                    "[FINALIZATION] Login-time repair marked stale run %s as failed for uuid=...%s "
                    "(reference=%s process_started=%s)",
                    run.id[:8],
                    user_uuid[-8:],
                    reference_time.isoformat(),
                    process_started_at.isoformat(),
                )
                failed_run = await run_repo.fail(
                    run.id,
                    error_message=(
                        "Run interrupted by API service restart. "
                        "Marked as failed during post-login recovery."
                    ),
                )
                if failed_run is not None:
                    await write_repair_metadata(
                        session,
                        run.id,
                        canonical_outcome=CANONICAL_OUTCOME_FAILED_INTERRUPTED,
                        repair_source=REPAIR_SOURCE_STALE_PRE_RESTART,
                    )
                evict_run_detail(user_uuid=user_uuid, run_id=run.id)
                summary["failed_stale"] = int(summary["failed_stale"]) + 1
                _increment_repair_outcome(summary, CANONICAL_OUTCOME_FAILED_INTERRUPTED)
                details.append(
                    _make_repair_detail(
                        failed_run or run,
                        canonical_outcome=CANONICAL_OUTCOME_FAILED_INTERRUPTED,
                        repair_source=REPAIR_SOURCE_STALE_PRE_RESTART,
                    )
                )
                continue

            summary["left_running"] = int(summary["left_running"]) + 1

    return summary
