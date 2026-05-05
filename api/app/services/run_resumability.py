"""
Helpers for deciding whether a run can resume from durable checkpoints.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from app.infra.db.models.run import RunStatus
from app.infra.db.repositories.run_results import RunResultsRepository
from app.infra.db.repositories.task import TaskRepository
from app.services.compiled_run_config import extract_compiled_run_config_payload

logger = logging.getLogger(__name__)


def _empty_phase_counts() -> Dict[str, int]:
    return {
        "total": 0,
        "pending": 0,
        "running": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "skipped": 0,
    }


def _normalized_checkpoint_summary(summary: Optional[Dict[str, Dict[str, int]]]) -> Dict[str, Dict[str, int]]:
    phases = ("generation", "single_eval", "pairwise", "combine", "all")
    normalized: Dict[str, Dict[str, int]] = {}
    raw = summary or {}
    for phase in phases:
        counts = _empty_phase_counts()
        counts.update({k: int(v or 0) for k, v in (raw.get(phase) or {}).items() if k in counts})
        normalized[phase] = counts
    return normalized


def _phase_hint(summary: Dict[str, Dict[str, int]]) -> Optional[str]:
    for phase in ("combine", "pairwise", "single_eval", "generation"):
        counts = summary.get(phase) or {}
        if counts.get("running", 0) > 0:
            return phase
        if counts.get("completed", 0) > 0 and counts.get("pending", 0) > 0:
            return phase
        if counts.get("failed", 0) > 0:
            return phase
    return None


def _normalized_model_names(raw_models: Any) -> list[str]:
    normalized: list[str] = []
    for raw in raw_models or []:
        if isinstance(raw, str):
            value = raw.strip()
        elif isinstance(raw, dict):
            provider = str(raw.get("provider", "") or "").strip()
            model = str(raw.get("model", "") or "").strip()
            value = f"{provider}:{model}" if provider and model else ""
        else:
            value = ""
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _run_eval_config(run: Any) -> Dict[str, Any]:
    config = getattr(run, "config", None)
    compiled = extract_compiled_run_config_payload(config)
    if isinstance(compiled, dict):
        raw = compiled.get("eval_config")
        if isinstance(raw, dict):
            return raw
    if isinstance(config, dict):
        raw = config.get("eval_config")
        if isinstance(raw, dict):
            return raw
    return {}


def _run_evaluation_enabled(run: Any) -> bool:
    config = getattr(run, "config", None)
    compiled = extract_compiled_run_config_payload(config)
    if isinstance(compiled, dict):
        eval_config = compiled.get("eval_config")
        return bool(compiled.get("enable_single_eval") or eval_config)
    if not isinstance(config, dict):
        return False
    if "evaluation_enabled" in config:
        return bool(config.get("evaluation_enabled"))
    return bool(config.get("eval_config"))


def _status_value(row: Any) -> str:
    return str(_row_value(row, "status", "") or "")


def _error_value(row: Any) -> str:
    return str(_row_value(row, "error_message", "") or "")


def _source_doc_status_is_degraded(row: Any) -> bool:
    status = _status_value(row)
    if status in (
        RunStatus.COMPLETED_WITH_ERRORS.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
    ):
        return True
    return bool(_error_value(row))


async def _detect_terminal_incomplete_resume_reason(
    *,
    db: Any,
    run: Any,
    checkpoint_summary: Optional[Dict[str, Dict[str, int]]] = None,
) -> Optional[str]:
    status = getattr(run, "status", None)
    if status == RunStatus.COMPLETED_WITH_ERRORS.value:
        return "Run finished with partial results and can resume missing work."

    if status != RunStatus.COMPLETED.value:
        return None

    summary = _normalized_checkpoint_summary(checkpoint_summary)
    all_counts = summary["all"]
    if any(all_counts.get(key, 0) > 0 for key in ("pending", "running", "failed", "cancelled")):
        return "Run is marked completed, but durable task state still shows unfinished work."

    results_repo = RunResultsRepository(db)
    source_doc_statuses = list(await results_repo.get_source_doc_statuses(getattr(run, "id")))
    if any(_source_doc_status_is_degraded(row) for row in source_doc_statuses):
        return "Run is marked completed, but durable per-document status shows partial results."

    if not _run_evaluation_enabled(run):
        return None

    eval_config = _run_eval_config(run)
    judge_models = _normalized_model_names(eval_config.get("judge_models"))
    eval_iterations = max(int(eval_config.get("iterations") or 1), 1)
    if not judge_models:
        return None

    generated_docs = list(await results_repo.get_generated_docs(getattr(run, "id")))
    generated_doc_ids = {
        str(_row_value(row, "doc_id", "") or "")
        for row in generated_docs
        if _row_value(row, "doc_id")
    }
    if not generated_doc_ids:
        return None

    eval_scores = list(await results_repo.get_eval_scores(getattr(run, "id")))
    actual_attempts = {
        (
            str(_row_value(row, "doc_id", "") or ""),
            str(_row_value(row, "judge_model", "") or ""),
            int(_row_value(row, "trial", 1) or 1),
        )
        for row in eval_scores
        if _row_value(row, "doc_id") in generated_doc_ids
        and _row_value(row, "judge_model")
    }
    expected_attempts = len(generated_doc_ids) * len(judge_models) * eval_iterations
    missing_attempts = max(expected_attempts - len(actual_attempts), 0)
    if missing_attempts > 0:
        return (
            f"Run is marked completed, but {missing_attempts} evaluation attempt(s) "
            f"are still missing and can be retried."
        )

    return None


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _normalized_doc_id_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(raw, (list, tuple, set)):
        return [str(part).strip() for part in raw if str(part).strip()]
    return []


def _count_reusable_pairwise_checkpoints(
    tasks: list[Any],
    rows: list[Any],
    *,
    model_name: str,
) -> int:
    completed_doc_ids = {
        str(getattr(task, "document_id", "") or "")
        for task in tasks
        if getattr(task, "status", None) == "completed"
        and getattr(task, "model_name", None) == model_name
        and getattr(task, "document_id", None)
    }
    reusable_doc_ids = {
        str(_row_value(row, "source_doc_id", "") or "")
        for row in rows
        if _row_value(row, "source_doc_id")
        and _row_value(row, "doc_id_a")
        and _row_value(row, "doc_id_b")
        and _row_value(row, "judge_model")
    }
    return len(completed_doc_ids & reusable_doc_ids)


def _count_reusable_combined_outputs(rows: list[Any]) -> int:
    latest_by_key: Dict[tuple[str, str], Any] = {}
    for row in rows:
        source_doc_id = str(_row_value(row, "source_doc_id", "") or "")
        combine_model = str(_row_value(row, "combine_model", "") or "")
        if not source_doc_id or not combine_model:
            continue
        key = (source_doc_id, combine_model)
        completed_at = _row_value(row, "completed_at")
        existing = latest_by_key.get(key)
        existing_completed_at = _row_value(existing, "completed_at") if existing is not None else None
        if existing is None or (completed_at and (existing_completed_at is None or completed_at > existing_completed_at)):
            latest_by_key[key] = row

    reusable = 0
    for row in latest_by_key.values():
        file_path = _row_value(row, "file_path")
        input_doc_ids = _normalized_doc_id_list(_row_value(row, "input_doc_ids"))
        if not isinstance(file_path, str) or not file_path.strip():
            continue
        if not input_doc_ids:
            continue
        if Path(file_path).exists():
            reusable += 1
    return reusable


def classify_run_resumability(
    run: Any,
    checkpoint_summary: Optional[Dict[str, Dict[str, int]]] = None,
    *,
    active_executor_present: bool = False,
    terminal_incomplete: bool = False,
    terminal_incomplete_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Return a canonical resumability decision for a run.

    The decision is intentionally conservative:
    - paused and failed runs are resumable when created from a preset
    - running/pending runs can be treated as interrupted if there is no live
      executor but there is evidence that work already started
    - cancelled/completed runs are not resumable in this pass
    """
    summary = _normalized_checkpoint_summary(checkpoint_summary)
    all_counts = summary["all"]
    has_checkpoint_activity = any(
        all_counts.get(key, 0) > 0 for key in ("completed", "failed", "running", "cancelled", "skipped")
    )
    started = bool(getattr(run, "started_at", None))
    terminal = bool(getattr(run, "completed_at", None))
    phase_hint = _phase_hint(summary)
    stale_running_tasks = int(all_counts.get("running", 0) or 0)

    warnings: list[str] = []
    blocking_errors: list[str] = []

    resumable = False
    resume_mode = "not_resumable"
    reason = "Run is not resumable."

    if not getattr(run, "preset_id", None):
        blocking_errors.append("Run was not created from a preset, so config cannot be rebuilt safely.")
        reason = "Run is not resumable because it was not created from a preset."
    else:
        status = getattr(run, "status", None)
        if status == RunStatus.PAUSED.value:
            resumable = True
            resume_mode = "paused"
            reason = "Run is paused and can continue from its existing checkpoints."
        elif status == RunStatus.FAILED.value:
            resumable = True
            resume_mode = "failed"
            reason = "Run failed but has durable state and can resume from where it left off."
        elif status in (RunStatus.RUNNING.value, RunStatus.PENDING.value):
            if active_executor_present:
                reason = "Run is currently active in this process and should not be resumed."
                blocking_errors.append("A live executor is still attached to this run.")
            elif terminal:
                reason = "Run has terminal timestamps but a non-terminal status; repair or snapshot should be used first."
                blocking_errors.append("Run state is inconsistent and should be reconciled before resume.")
            elif started or has_checkpoint_activity:
                resumable = True
                resume_mode = "interrupted"
                reason = (
                    "Run appears interrupted: it is marked active, but no live executor is attached. "
                    "Resume will reuse completed checkpoints and retry unfinished work."
                )
                if stale_running_tasks:
                    warnings.append(
                        f"{stale_running_tasks} in-flight task(s) will be reset from running to pending before resume."
                    )
            else:
                reason = "Run has not started any durable work yet; use start instead of resume."
        elif status == RunStatus.CANCELLED.value:
            reason = "Cancelled runs are not resumable in the current product model."
        elif status in (RunStatus.COMPLETED.value, RunStatus.COMPLETED_WITH_ERRORS.value):
            if terminal_incomplete:
                resumable = True
                resume_mode = "terminal_incomplete"
                reason = (
                    terminal_incomplete_reason
                    or "Run reached a terminal status, but durable state shows missing work that can be resumed."
                )
            else:
                reason = "Completed runs are already complete and have nothing left to resume."

    if phase_hint and resumable:
        warnings.append(f"Resume checkpoint coverage indicates progress had reached the {phase_hint} phase.")

    return {
        "run_id": getattr(run, "id", ""),
        "run_status": getattr(run, "status", ""),
        "resumable": resumable,
        "resume_mode": resume_mode,
        "reason": reason,
        "has_active_executor": active_executor_present,
        "requires_preset": bool(getattr(run, "preset_id", None)),
        "phase_hint": phase_hint,
        "stale_running_tasks": stale_running_tasks,
        "reusable_generation_tasks": int(summary["generation"].get("completed", 0) or 0),
        "reusable_eval_tasks": int(summary["single_eval"].get("completed", 0) or 0),
        "reusable_pairwise_tasks": int(summary["pairwise"].get("completed", 0) or 0),
        "reusable_combine_tasks": int(summary["combine"].get("completed", 0) or 0),
        "checkpoint_summary": summary,
        "warnings": warnings,
        "blocking_errors": blocking_errors,
    }


async def build_run_resume_info(
    *,
    db: Any,
    run: Any,
    active_executor_present: bool = False,
) -> Dict[str, Any]:
    run_id = getattr(run, "id", "")
    task_repo = TaskRepository(db)
    checkpoint_summary = await task_repo.get_checkpoint_summary(run_id)
    summary = _normalized_checkpoint_summary(checkpoint_summary)
    compiled = extract_compiled_run_config_payload(getattr(run, "config", None))
    if not isinstance(compiled, dict):
        info = {
            "run_id": run_id,
            "run_status": getattr(run, "status", ""),
            "resumable": False,
            "resume_mode": "not_resumable",
            "reason": "Run is missing compiled_config and cannot be resumed; legacy run snapshots are no longer supported.",
            "has_active_executor": active_executor_present,
            "requires_preset": bool(getattr(run, "preset_id", None)),
            "phase_hint": _phase_hint(summary),
            "stale_running_tasks": int(summary["all"].get("running", 0) or 0),
            "reusable_generation_tasks": int(summary["generation"].get("completed", 0) or 0),
            "reusable_eval_tasks": int(summary["single_eval"].get("completed", 0) or 0),
            "reusable_pairwise_tasks": int(summary["pairwise"].get("completed", 0) or 0),
            "reusable_combine_tasks": int(summary["combine"].get("completed", 0) or 0),
            "checkpoint_summary": summary,
            "warnings": [],
            "blocking_errors": [
                "Run is missing compiled_config and cannot be resumed safely.",
            ],
            "reusable_pre_combine_pairwise_tasks": 0,
            "reusable_post_combine_pairwise_tasks": 0,
        }
        logger.warning(
            "[RESUME INFO] run=%s status=%s resumable=%s reason=%s compiled_config_present=%s",
            run_id,
            getattr(run, "status", ""),
            info["resumable"],
            info["reason"],
            bool(isinstance(getattr(run, "config", None), dict) and getattr(run, "config", {}).get("compiled_config")),
        )
        return info

    terminal_incomplete_reason: Optional[str] = None
    try:
        terminal_incomplete_reason = await _detect_terminal_incomplete_resume_reason(
            db=db,
            run=run,
            checkpoint_summary=checkpoint_summary,
        )
    except Exception as exc:
        logger.warning(
            "[RESUME INFO] run=%s failed to detect terminal incomplete state: %s",
            run_id,
            exc,
        )
        terminal_incomplete_reason = None
    info = classify_run_resumability(
        run,
        checkpoint_summary,
        active_executor_present=active_executor_present,
        terminal_incomplete=bool(terminal_incomplete_reason),
        terminal_incomplete_reason=terminal_incomplete_reason,
    )
    info["reusable_pre_combine_pairwise_tasks"] = 0
    info["reusable_post_combine_pairwise_tasks"] = 0

    try:
        pairwise_tasks = list(await task_repo.get_tasks_by_phase(run_id, "pairwise"))
        results_repo = RunResultsRepository(db)
        pre_rows = list(
            await results_repo.get_pairwise_results(
                run_id,
                comparison_type="pre_combine",
            )
        )
        post_rows = list(
            await results_repo.get_pairwise_results(
                run_id,
                comparison_type="post_combine",
            )
        )
        combined_rows = list(await results_repo.get_combined_docs(run_id))

        reusable_pre = _count_reusable_pairwise_checkpoints(
            pairwise_tasks,
            pre_rows,
            model_name="pre_combine_summary",
        )
        reusable_post = _count_reusable_pairwise_checkpoints(
            pairwise_tasks,
            post_rows,
            model_name="post_combine_summary",
        )
        info["reusable_pre_combine_pairwise_tasks"] = reusable_pre
        info["reusable_post_combine_pairwise_tasks"] = reusable_post
        info["reusable_pairwise_tasks"] = reusable_pre + reusable_post
        info["reusable_combine_tasks"] = _count_reusable_combined_outputs(combined_rows)
    except Exception as exc:
        logger.warning(
            "[RESUME INFO] run=%s failed to calculate reusable pairwise/combine checkpoints: %s",
            run_id,
            exc,
        )

    logger.info(
        "[RESUME INFO] run=%s status=%s resumable=%s mode=%s reason=%s phase_hint=%s reusable_generation=%s reusable_eval=%s reusable_pairwise=%s reusable_combine=%s stale_running=%s",
        run_id,
        getattr(run, "status", ""),
        info["resumable"],
        info["resume_mode"],
        info["reason"],
        info.get("phase_hint"),
        info["reusable_generation_tasks"],
        info["reusable_eval_tasks"],
        info["reusable_pairwise_tasks"],
        info["reusable_combine_tasks"],
        info["stale_running_tasks"],
    )

    return info
