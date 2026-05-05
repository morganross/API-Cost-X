"""
Lean run-detail builders for the API read path.

These helpers deliberately avoid the old "full snapshot every time" behavior.
They build a small base payload first, then attach only the requested sections.
"""
from __future__ import annotations

from typing import Any, Optional

from app.services.results_reader import RunResultsSnapshot
from app.services.compiled_run_config import extract_compiled_run_config_payload

from ...schemas.runs import (
    DocumentEvalDetail,
    FpfStats,
    GeneratedDocInfo,
    PairwiseResults,
    RunDetail,
    RunEstimateSnapshot,
    SourceDocResultResponse,
    SourceDocStatus,
    TaskSummary,
    TimelineEvent,
)
from .helpers import (
    _group_snapshot_by_source_doc,
    _parse_metadata_json,
    _rebuild_pairwise_from_normalized,
    _rebuild_post_combine_evals_detailed,
    _rebuild_post_combine_evals_dict,
    _rebuild_pre_combine_evals_detailed,
    calculate_progress,
    get_fpf_stats_from_metadata,
    get_locked_invariants_from_metadata,
)


def _parse_run_detail_config(run) -> dict[str, Any]:
    config = run.config or {}
    compiled = extract_compiled_run_config_payload(config)
    if compiled:
        run_estimate = None
        general_config = compiled.get("general_config") or {}
        if isinstance(general_config, dict) and general_config.get("run_estimate"):
            try:
                run_estimate = RunEstimateSnapshot(**general_config["run_estimate"])
            except Exception:
                run_estimate = None

        return {
            "config": config,
            "run_estimate": run_estimate,
            "tags": config.get("tags") or [],
        }

    general_config = config.get("general_config") or {}
    run_estimate = None
    if isinstance(general_config, dict) and general_config.get("run_estimate"):
        try:
            run_estimate = RunEstimateSnapshot(**general_config["run_estimate"])
        except Exception:
            run_estimate = None

    return {
        "config": config,
        "run_estimate": run_estimate,
        "tags": config.get("tags") or [],
    }


def build_run_detail_base(
    run,
    *,
    metadata: Optional[dict[str, str]] = None,
) -> RunDetail:
    parsed = _parse_run_detail_config(run)
    resolved_metadata = metadata or {}
    detail = RunDetail(
        id=run.id,
        name=run.title or "Untitled",
        description=run.description,
        status=run.status,
        error_message=run.error_message,
        preset_id=getattr(run, "preset_id", None),
        pause_requested=getattr(run, "pause_requested", 0) or 0,
        resume_count=getattr(run, "resume_count", 0) or 0,
        progress=calculate_progress(run),
        run_estimate=parsed["run_estimate"],
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        total_duration_seconds=None,
        tags=parsed["tags"],
        fpf_stats=get_fpf_stats_from_metadata(run.id, resolved_metadata),
        locked_invariants=get_locked_invariants_from_metadata(run.id, resolved_metadata),
    )
    return detail


def build_task_summaries(tasks: list[Any]) -> list[TaskSummary]:
    return [
        TaskSummary(
            id=t.id,
            document_id=t.document_id,
            document_name=t.document_id,
            generator=t.generator or "fpf",
            model=t.model_name,
            iteration=t.iteration,
            status=t.status,
            duration_seconds=t.duration_seconds,
            started_at=t.started_at,
            completed_at=t.completed_at,
            error_message=t.error_message,
        )
        for t in (tasks or [])
    ]


def _generated_doc_from_row(row: dict[str, Any]) -> Optional[GeneratedDocInfo]:
    doc_id = row.get("doc_id", "")
    if not doc_id:
        return None
    return GeneratedDocInfo(
        id=doc_id,
        model=row.get("model", ""),
        source_doc_id=row.get("source_doc_id", ""),
        generator=row.get("generator", ""),
        iteration=row.get("iteration", 1),
        completion_status="complete",
    )


def _combined_doc_from_row(row: dict[str, Any]) -> Optional[GeneratedDocInfo]:
    doc_id = row.get("doc_id", "")
    if not doc_id:
        return None
    return GeneratedDocInfo(
        id=doc_id,
        model=row.get("combine_model", ""),
        source_doc_id=row.get("source_doc_id", ""),
        generator=row.get("combine_strategy", ""),
        iteration=1,
        completion_status="complete",
    )


def build_generated_docs(snapshot: RunResultsSnapshot) -> list[GeneratedDocInfo]:
    return [
        parsed
        for parsed in (_generated_doc_from_row(row) for row in (snapshot.generated_docs or []))
        if parsed is not None
    ]


def build_combined_doc_ids(snapshot: RunResultsSnapshot) -> list[str]:
    return [row.get("doc_id", "") for row in (snapshot.combined_docs or []) if row.get("doc_id")]


def _timeline_event_from_row(row: dict[str, Any]) -> TimelineEvent:
    import json

    details_raw = row.get("details_json")
    details = {}
    if details_raw:
        try:
            details = json.loads(details_raw) if isinstance(details_raw, str) else details_raw
        except Exception:
            details = {}
    return TimelineEvent(
        phase=row.get("phase", "unknown"),
        event_type=row.get("event_type", "unknown"),
        description=row.get("description") or "",
        model=row.get("model"),
        timestamp=row.get("occurred_at"),
        duration_seconds=row.get("duration_seconds"),
        success=row.get("success", True),
        details=details,
    )


def build_timeline_events(snapshot: RunResultsSnapshot) -> list[TimelineEvent]:
    return [_timeline_event_from_row(row) for row in (snapshot.timeline_events or [])]


def build_pairwise_results(snapshot: RunResultsSnapshot, comparison_type: str = "pre_combine") -> Optional[PairwiseResults]:
    return _rebuild_pairwise_from_normalized(snapshot.pairwise_results or [], comparison_type=comparison_type)


def build_pre_combine_evals_detailed(snapshot: RunResultsSnapshot) -> dict[str, DocumentEvalDetail]:
    combined_doc_ids = set(build_combined_doc_ids(snapshot))
    pre_scores = [
        row
        for row in (snapshot.eval_scores_raw or [])
        if row.get("doc_id", "") not in combined_doc_ids
    ]
    return _rebuild_pre_combine_evals_detailed(pre_scores) if pre_scores else {}


def build_post_combine_evals_detailed(snapshot: RunResultsSnapshot) -> dict[str, DocumentEvalDetail]:
    combined_doc_ids = set(build_combined_doc_ids(snapshot))
    if not combined_doc_ids:
        return {}
    return _rebuild_post_combine_evals_detailed(snapshot.eval_scores_raw or [], combined_doc_ids)


def build_post_combine_evals(snapshot: RunResultsSnapshot) -> dict[str, dict[str, float]]:
    combined_doc_ids = set(build_combined_doc_ids(snapshot))
    if not combined_doc_ids:
        return {}
    return _rebuild_post_combine_evals_dict(snapshot.eval_scores_raw or [], combined_doc_ids)


def _build_single_eval_scores(
    eval_scores: list[dict[str, Any]],
    *,
    allowed_doc_ids: Optional[set[str]] = None,
) -> dict[str, float]:
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for row in eval_scores or []:
        doc_id = row.get("doc_id") or ""
        if not doc_id or (allowed_doc_ids is not None and doc_id not in allowed_doc_ids):
            continue
        try:
            score = float(row.get("score") or 0.0)
        except Exception:
            continue
        totals[doc_id] = totals.get(doc_id, 0.0) + score
        counts[doc_id] = counts.get(doc_id, 0) + 1
    return {
        doc_id: (totals[doc_id] / counts[doc_id])
        for doc_id in totals.keys()
        if counts.get(doc_id)
    }


def _aggregate_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _build_single_eval_scores_from_aggregates(
    eval_aggregates: list[Any],
    *,
    allowed_doc_ids: Optional[set[str]] = None,
) -> dict[str, float]:
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for row in eval_aggregates or []:
        doc_id = str(_aggregate_value(row, "doc_id", "") or "")
        if not doc_id or (allowed_doc_ids is not None and doc_id not in allowed_doc_ids):
            continue
        try:
            avg_score = float(_aggregate_value(row, "avg_score", 0.0) or 0.0)
            trial_count = int(_aggregate_value(row, "trial_count", 0) or 0)
        except Exception:
            continue
        weight = trial_count if trial_count > 0 else 1
        totals[doc_id] = totals.get(doc_id, 0.0) + (avg_score * weight)
        counts[doc_id] = counts.get(doc_id, 0) + weight
    return {
        doc_id: (totals[doc_id] / counts[doc_id])
        for doc_id in totals.keys()
        if counts.get(doc_id)
    }


def _build_post_combine_eval_scores_from_aggregates(
    eval_aggregates: list[Any],
    combined_doc_id: str,
) -> dict[str, float]:
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for row in eval_aggregates or []:
        doc_id = str(_aggregate_value(row, "doc_id", "") or "")
        if doc_id != combined_doc_id:
            continue
        judge_model = str(_aggregate_value(row, "judge_model", "") or "")
        if not judge_model:
            continue
        try:
            avg_score = float(_aggregate_value(row, "avg_score", 0.0) or 0.0)
            trial_count = int(_aggregate_value(row, "trial_count", 0) or 0)
        except Exception:
            continue
        weight = trial_count if trial_count > 0 else 1
        totals[judge_model] = totals.get(judge_model, 0.0) + (avg_score * weight)
        counts[judge_model] = counts.get(judge_model, 0) + weight
    return {
        judge_model: (totals[judge_model] / counts[judge_model])
        for judge_model in totals.keys()
        if counts.get(judge_model)
    }


def _strip_pairwise_comparisons(
    pairwise_results: Optional[PairwiseResults],
) -> Optional[PairwiseResults]:
    if pairwise_results is None:
        return None
    return PairwiseResults(
        total_comparisons=pairwise_results.total_comparisons,
        winner_doc_id=pairwise_results.winner_doc_id,
        rankings=list(pairwise_results.rankings or []),
        comparisons=[],
        pairwise_deviations=dict(pairwise_results.pairwise_deviations or {}),
    )



def build_source_doc_results(
    snapshot: RunResultsSnapshot,
    *,
    source_doc_id: Optional[str] = None,
    include_single_eval_detailed: bool = True,
    include_timeline_events: bool = True,
    include_eval_deviations: bool = True,
    include_pairwise_results: bool = True,
    include_pairwise_comparisons: bool = True,
    include_generated_docs: bool = True,
    include_combined_docs: bool = True,
    include_score_summaries: bool = True,
) -> dict[str, SourceDocResultResponse]:
    groups = _group_snapshot_by_source_doc(snapshot)
    status_lookup = {
        row.get("source_doc_id", ""): row
        for row in (snapshot.source_doc_statuses or [])
        if row.get("source_doc_id")
    }
    pre_combine_detailed = (
        build_pre_combine_evals_detailed(snapshot)
        if include_single_eval_detailed
        else {}
    )
    post_combine_scores = build_post_combine_evals(snapshot)
    eval_deviations = (
        _parse_metadata_json(snapshot.metadata or {}, "eval_deviations")
        if include_eval_deviations
        else None
    )

    results: dict[str, SourceDocResultResponse] = {}
    all_source_doc_ids = sorted(set(groups.keys()) | set(status_lookup.keys()))
    for current_source_doc_id in all_source_doc_ids:
        if source_doc_id and current_source_doc_id != source_doc_id:
            continue

        group = groups.get(
            current_source_doc_id,
            {
                "generated_docs": [],
                "eval_scores": [],
                "pairwise_results": [],
                "timeline_events": [],
                "combined_docs": [],
            },
        )
        status_row = status_lookup.get(current_source_doc_id, {})

        generated_docs = [
            parsed
            for parsed in (_generated_doc_from_row(row) for row in group["generated_docs"])
            if parsed is not None
        ]
        generated_doc_count = len(generated_docs)
        generated_doc_ids = {doc.id for doc in generated_docs}
        combined_docs = [
            parsed
            for parsed in (_combined_doc_from_row(row) for row in group["combined_docs"])
            if parsed is not None
        ]
        combined_doc_count = len(combined_docs)
        combined_doc = combined_docs[0] if combined_docs else None
        aggregate_rows = [
            row
            for row in (snapshot.eval_aggregates or [])
            if str(_aggregate_value(row, "source_doc_id", "") or "") == current_source_doc_id
        ]

        if include_single_eval_detailed:
            single_eval_detailed = {
                doc.id: pre_combine_detailed[doc.id]
                for doc in generated_docs
                if doc.id in pre_combine_detailed
            }
            single_eval_scores = {
                doc_id: detail.overall_average
                for doc_id, detail in single_eval_detailed.items()
            }
        else:
            single_eval_detailed = {}
            single_eval_scores = (
                _build_single_eval_scores_from_aggregates(
                    aggregate_rows,
                    allowed_doc_ids=generated_doc_ids,
                )
                if aggregate_rows
                else _build_single_eval_scores(
                    group["eval_scores"],
                    allowed_doc_ids=generated_doc_ids,
                )
            )
        single_eval_score_count = len(single_eval_scores)
        if combined_doc:
            post_combine_eval_scores = (
                _build_post_combine_eval_scores_from_aggregates(
                    aggregate_rows,
                    combined_doc.id,
                )
                if aggregate_rows
                else {}
            )
            if not post_combine_eval_scores and combined_doc.id in post_combine_scores:
                raw_scores = post_combine_scores.get(combined_doc.id) or {}
                if isinstance(raw_scores, dict):
                    for judge_model, score in raw_scores.items():
                        try:
                            post_combine_eval_scores[str(judge_model)] = float(score)
                        except Exception:
                            continue
        else:
            post_combine_eval_scores = {}
        post_combine_eval_score_count = len(post_combine_eval_scores)
        status_value = status_row.get("status", "pending")
        try:
            parsed_status = SourceDocStatus(status_value)
        except ValueError:
            parsed_status = SourceDocStatus.PENDING

        started_at = status_row.get("started_at")
        completed_at = status_row.get("completed_at")
        duration_seconds = 0.0
        if started_at and completed_at:
            try:
                duration_seconds = max((completed_at - started_at).total_seconds(), 0.0)
            except Exception:
                duration_seconds = 0.0

        pairwise_results = None
        post_combine_pairwise = None
        if include_pairwise_results:
            pairwise_results = _rebuild_pairwise_from_normalized(
                group["pairwise_results"],
                comparison_type="pre_combine",
            )
            post_combine_pairwise = _rebuild_pairwise_from_normalized(
                group["pairwise_results"],
                comparison_type="post_combine",
            )
            if not include_pairwise_comparisons:
                pairwise_results = _strip_pairwise_comparisons(pairwise_results)
                post_combine_pairwise = _strip_pairwise_comparisons(post_combine_pairwise)

        if not include_generated_docs:
            generated_docs = []
        if not include_combined_docs:
            combined_doc = None
            combined_docs = []
        if not include_score_summaries:
            single_eval_scores = {}
            post_combine_eval_scores = {}

        results[current_source_doc_id] = SourceDocResultResponse(
            source_doc_id=current_source_doc_id,
            source_doc_name=status_row.get("source_doc_name") or current_source_doc_id,
            status=parsed_status,
            generated_docs=generated_docs,
            generated_doc_count=generated_doc_count,
            single_eval_scores=single_eval_scores,
            single_eval_score_count=single_eval_score_count,
            single_eval_detailed=single_eval_detailed,
            pairwise_results=pairwise_results,
            winner_doc_id=status_row.get("winner_doc_id"),
            combined_doc=combined_doc,
            combined_docs=combined_docs,
            combined_doc_count=combined_doc_count,
            post_combine_eval_scores=post_combine_eval_scores,
            post_combine_eval_score_count=post_combine_eval_score_count,
            post_combine_pairwise=post_combine_pairwise,
            timeline_events=[
                _timeline_event_from_row(row) for row in group["timeline_events"]
            ] if include_timeline_events else [],
            errors=[status_row.get("error_message")] if status_row.get("error_message") else [],
            duration_seconds=duration_seconds,
            started_at=started_at,
            completed_at=completed_at,
            eval_deviations=eval_deviations,
        )
    return results


def build_full_run_detail(
    run,
    snapshot: RunResultsSnapshot,
) -> RunDetail:
    detail = build_run_detail_base(
        run,
        metadata=snapshot.metadata,
    )
    detail.tasks = build_task_summaries(list(run.tasks or []))
    detail.winner = snapshot.metadata.get("winner")
    detail.generated_docs = build_generated_docs(snapshot)
    detail.post_combine_evals = build_post_combine_evals(snapshot)
    detail.pairwise_results = build_pairwise_results(snapshot, "pre_combine")
    detail.post_combine_pairwise = build_pairwise_results(snapshot, "post_combine")
    detail.combined_doc_ids = build_combined_doc_ids(snapshot)
    detail.pre_combine_evals_detailed = build_pre_combine_evals_detailed(snapshot)
    detail.post_combine_evals_detailed = build_post_combine_evals_detailed(snapshot)
    detail.eval_deviations = _parse_metadata_json(snapshot.metadata, "eval_deviations") or {}
    detail.criteria_list = snapshot.criteria_list or []
    detail.evaluator_list = snapshot.evaluator_list or []
    detail.timeline_events = build_timeline_events(snapshot)
    detail.source_doc_results = build_source_doc_results(snapshot)
    return detail
