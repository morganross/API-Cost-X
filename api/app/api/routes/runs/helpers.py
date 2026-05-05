"""
Helper functions for run data transformation.

Contains serialization and conversion utilities for runs.
"""
import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import inspect

from app.services.results_reader import RunResultsSnapshot
from app.services.compiled_run_config import extract_compiled_run_config_payload

from ...schemas.runs import (
    RunProgress,
    RunSummary,
    TaskStatus,
    FpfStats,
    LockedInvariantRule,
    LockedInvariants,
    RunEstimateSnapshot,
    PairwiseResults,
    PairwiseRanking,
    DocumentEvalDetail,
    JudgeEvaluation,
    CriterionScoreInfo,
    PairwiseComparison,
)

logger = logging.getLogger(__name__)


def serialize_dataclass(obj: Any) -> Any:
    """
    Recursively convert a dataclass to a dict, serializing datetime objects to ISO strings.
    """
    if obj is None:
        return None
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, (list, tuple)):
        return [serialize_dataclass(item) for item in obj]
    if isinstance(obj, dict):
        return {k: serialize_dataclass(v) for k, v in obj.items()}
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: serialize_dataclass(v) for k, v in asdict(obj).items()}
    return obj


def calculate_progress(run) -> RunProgress:
    """Calculate progress for a run.

    NOTE: This function requires tasks to be eagerly loaded. Use get_with_tasks()
    or get_all_with_tasks() to fetch runs before calling this.
    """
    # Check if tasks is loaded without triggering a query
    insp = inspect(run)
    if 'tasks' not in insp.dict:
        # Tasks not loaded, return estimate from run's stored counters.
        raw_total = max(int(run.total_tasks or 0), 0)
        completed = max(int(run.completed_tasks or 0), 0)
        failed = max(int(run.failed_tasks or 0), 0)
        derived_total = max(raw_total, completed + failed)
        return RunProgress(
            total_tasks=derived_total,
            completed_tasks=completed,
            running_tasks=0,
            failed_tasks=failed,
            pending_tasks=max(0, derived_total - completed - failed),
            progress_percent=min(100.0, ((completed / derived_total) * 100) if derived_total else 0.0),
        )

    tasks = run.tasks or []
    total = len(tasks)

    # If no tasks yet, estimate from config
    if not tasks:
        return RunProgress(
            total_tasks=0,
            completed_tasks=0,
            running_tasks=0,
            failed_tasks=0,
            pending_tasks=0,
            progress_percent=0.0,
        )

    completed = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)
    running = sum(1 for t in tasks if t.status == TaskStatus.RUNNING)
    failed = sum(1 for t in tasks if t.status == TaskStatus.FAILED)
    pending = total - completed - running - failed

    safe_total = max(total, completed + running + failed)

    return RunProgress(
        total_tasks=safe_total,
        completed_tasks=completed,
        running_tasks=running,
        failed_tasks=failed,
        pending_tasks=max(0, safe_total - completed - running - failed),
        progress_percent=min(100.0, (completed / safe_total * 100) if safe_total > 0 else 0.0),
    )


def to_summary(run) -> RunSummary:
    """Convert DB run to summary response."""
    config = run.config or {}
    compiled = extract_compiled_run_config_payload(config)

    if compiled:
        general_config = compiled.get("general_config") or {}
        run_estimate = None
        if isinstance(general_config, dict) and general_config.get("run_estimate"):
            try:
                run_estimate = RunEstimateSnapshot(**general_config["run_estimate"])
            except Exception:
                run_estimate = None
    else:
        general_config = config.get("general_config") or {}
        run_estimate = None
        if isinstance(general_config, dict) and general_config.get("run_estimate"):
            try:
                run_estimate = RunEstimateSnapshot(**general_config["run_estimate"])
            except Exception:
                run_estimate = None

    return RunSummary(
        id=run.id,
        name=run.title or "Untitled",
        description=run.description,
        status=run.status,
        error_message=run.error_message,  # Include error message from DB
        progress=calculate_progress(run),
        run_estimate=run_estimate,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        tags=config.get("tags") or [],
    )


# ---------------------------------------------------------------------------
# Helpers: rebuild score structures from normalized run_eval_scores rows
# These are used by the canonical run-detail builders in detail_payload.py.
# ---------------------------------------------------------------------------

def _rebuild_pre_combine_evals_detailed(
    eval_scores_raw: list[dict],
) -> dict:
    """
    Rebuild pre_combine_evals_detailed from raw run_eval_scores rows.

    Input: list of dicts with keys: doc_id, criterion, judge_model, trial, score, reason
    Output: {doc_id: DocumentEvalDetail(evaluations=[...], overall_average=float)}
    """
    from collections import defaultdict

    # doc_id -> (judge_model, trial) -> list of {criterion, score, reason}
    grouped: dict[str, dict[tuple, list]] = defaultdict(lambda: defaultdict(list))
    for row in eval_scores_raw:
        doc_id = row.get("doc_id") or ""
        judge = row.get("judge_model") or ""
        trial = int(row.get("trial") or 1)
        grouped[doc_id][(judge, trial)].append({
            "criterion": row.get("criterion") or "",
            "score": int(row.get("score") or 0),
            "reason": row.get("reason") or "",
        })

    result = {}
    for doc_id, judge_trials in grouped.items():
        if not doc_id:
            continue
        evaluations = []
        for (judge, trial), scores in sorted(judge_trials.items()):
            avg = sum(s["score"] for s in scores) / len(scores) if scores else 0.0
            evaluations.append(JudgeEvaluation(
                judge_model=judge,
                trial=trial,
                scores=[
                    CriterionScoreInfo(
                        criterion=s["criterion"],
                        score=s["score"],
                        reason=s["reason"],
                    )
                    for s in scores
                ],
                average_score=avg,
            ))
        if not evaluations:
            continue
        overall = sum(e.average_score for e in evaluations) / len(evaluations)
        result[doc_id] = DocumentEvalDetail(
            evaluations=evaluations,
            overall_average=overall,
        )
    return result


def _rebuild_pairwise_from_normalized(
    rows: list[dict], comparison_type: str = None
) -> Optional[PairwiseResults]:
    """Rebuild PairwiseResults from normalized run_pairwise_results rows."""
    if comparison_type:
        rows = [r for r in rows if r.get("comparison_type") == comparison_type]
    if not rows:
        return None

    win_counts: dict[str, int] = {}
    loss_counts: dict[str, int] = {}
    all_docs: set[str] = set()
    for r in rows:
        a = r.get("doc_id_a", "")
        b = r.get("doc_id_b", "")
        w = r.get("winner_doc_id")
        all_docs.add(a)
        all_docs.add(b)
        if w == a:
            win_counts[a] = win_counts.get(a, 0) + 1
            loss_counts[b] = loss_counts.get(b, 0) + 1
        elif w == b:
            win_counts[b] = win_counts.get(b, 0) + 1
            loss_counts[a] = loss_counts.get(a, 0) + 1

    rankings = []
    for doc_id in sorted(all_docs):
        wins = win_counts.get(doc_id, 0)
        losses = loss_counts.get(doc_id, 0)
        elo = 1500.0 + (wins - losses) * 50.0
        rankings.append(PairwiseRanking(doc_id=doc_id, wins=wins, losses=losses, elo=elo))
    rankings.sort(key=lambda r: r.wins, reverse=True)

    comparisons = [
        PairwiseComparison(
            doc_id_a=r.get("doc_id_a", ""),
            doc_id_b=r.get("doc_id_b", ""),
            winner=r.get("winner_doc_id"),
            judge_model=r.get("judge_model", ""),
            reason=r.get("reason", ""),
            score_a=None,
            score_b=None,
        )
        for r in rows
    ]

    winner = rankings[0].doc_id if rankings else None
    return PairwiseResults(
        total_comparisons=len(rows),
        winner_doc_id=winner,
        rankings=rankings,
        comparisons=comparisons,
        pairwise_deviations={},
    )


def _rebuild_post_combine_evals_dict(
    eval_scores_raw: list[dict], combined_doc_ids: set[str]
) -> dict:
    """Rebuild {combined_doc_id: {judge_model: avg_score}} from eval scores for combined docs."""
    from collections import defaultdict
    result: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in eval_scores_raw:
        doc_id = row.get("doc_id", "")
        if doc_id in combined_doc_ids:
            judge = row.get("judge_model", "")
            score = float(row.get("score", 0))
            result[doc_id][judge].append(score)
    return {
        doc_id: {judge: sum(scores) / len(scores) for judge, scores in judges.items()}
        for doc_id, judges in result.items()
    }


def _rebuild_post_combine_evals_detailed(
    eval_scores_raw: list[dict], combined_doc_ids: set[str]
) -> dict:
    """Rebuild post_combine_evals_detailed from eval scores for combined docs."""
    from collections import defaultdict
    # Filter to combined docs only
    combined_scores = [r for r in eval_scores_raw if r.get("doc_id", "") in combined_doc_ids]
    if not combined_scores:
        return {}
    # Reuse the pre_combine builder — same structure, different docs
    return _rebuild_pre_combine_evals_detailed(combined_scores)


def _group_snapshot_by_source_doc(snapshot: RunResultsSnapshot) -> dict:
    """Group snapshot flat lists into per-source-doc buckets for source_doc_results."""
    from collections import defaultdict
    groups: dict[str, dict] = defaultdict(lambda: {
        "generated_docs": [],
        "eval_scores": [],
        "pairwise_results": [],
        "timeline_events": [],
        "combined_docs": [],
    })
    for d in snapshot.generated_docs:
        sid = d.get("source_doc_id", "")
        if sid:
            groups[sid]["generated_docs"].append(d)
    for s in snapshot.eval_scores_raw:
        sid = s.get("source_doc_id", "")
        if sid:
            groups[sid]["eval_scores"].append(s)
    for p in snapshot.pairwise_results:
        sid = p.get("source_doc_id", "")
        if sid:
            groups[sid]["pairwise_results"].append(p)
    for t in snapshot.timeline_events:
        sid = t.get("source_doc_id")
        if sid:
            groups[sid]["timeline_events"].append(t)
    for c in snapshot.combined_docs:
        sid = c.get("source_doc_id", "")
        if sid:
            groups[sid]["combined_docs"].append(c)
    return dict(groups)


def _parse_metadata_json(metadata: dict, key: str, default=None):
    """Parse a JSON-encoded value from the metadata KV dict.

    Metadata values are stored as strings in the run_metadata table.
    This helper safely deserialises them back to dicts/lists.
    """
    import json
    raw = metadata.get(key)
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)  # law-exempt: deserializing DB metadata column (stored as JSON string, not an accumulator)
    except (json.JSONDecodeError, TypeError):
        logger.warning(f"[_parse_metadata_json] Failed to parse key={key!r}: {raw!r:.120}")
        return default


def get_fpf_stats_from_metadata(run_id: str, metadata: dict) -> Optional[FpfStats]:
    """Extract FPF stats from normalized metadata with robust error handling."""
    try:
        raw = metadata.get("fpf_stats")
        if not raw:
            logger.debug(f"[STATS] No fpf_stats in metadata for run {run_id}")
            return None
        # metadata values are stored as strings; parse JSON if needed
        stats_data = json.loads(raw) if isinstance(raw, str) else raw  # law-exempt: DB metadata column read
        if not isinstance(stats_data, dict):
            logger.warning(f"[STATS] Invalid fpf_stats type for run {run_id}: {type(stats_data)}")
            return None
        fpf_stats = FpfStats(**stats_data)
        logger.debug(f"[STATS] Successfully retrieved fpf_stats for run {run_id}: total={fpf_stats.total_calls} success={fpf_stats.successful_calls}")
        return fpf_stats
    except Exception as e:
        logger.error(f"[STATS] Failed to parse fpf_stats for run {run_id}: {e}", exc_info=True)
        return None


def get_locked_invariants_from_metadata(run_id: str, metadata: dict) -> LockedInvariants:
    """Build the read-only locked invariant status object from run metadata."""
    fpf_stats = get_fpf_stats_from_metadata(run_id, metadata) if metadata else None
    raw_failures = _parse_metadata_json(metadata or {}, "invariant_failures", default=[])
    failures = [entry for entry in (raw_failures or []) if isinstance(entry, dict)]
    latest_failure = failures[-1] if failures else None
    failure_count = len(failures)
    has_successful_fpf_call = bool(fpf_stats and (fpf_stats.successful_calls or 0) > 0)

    search_status = "unknown"
    reasoning_status = "unknown"
    if latest_failure:
        reasoning_status = "failed"
    elif has_successful_fpf_call:
        search_status = "passed"
        reasoning_status = "passed"

    return LockedInvariants(
        search=LockedInvariantRule(
            mode="locked",
            enforced=True,
            status=search_status,
        ),
        reasoning_grounding=LockedInvariantRule(
            mode="locked",
            enforced=True,
            status=reasoning_status,
            failure_type=latest_failure.get("failure_type") if latest_failure else None,
            message=latest_failure.get("message") if latest_failure else None,
            provider=latest_failure.get("provider") if latest_failure else None,
            model=latest_failure.get("model") if latest_failure else None,
            source=latest_failure.get("source") if latest_failure else None,
            source_doc_id=latest_failure.get("source_doc_id") if latest_failure else None,
            source_doc_name=latest_failure.get("source_doc_name") if latest_failure else None,
            task_id=latest_failure.get("task_id") if latest_failure else None,
            failure_count=failure_count,
        ),
    )
