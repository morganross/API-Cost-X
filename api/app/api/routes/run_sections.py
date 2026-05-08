"""
Computed analytics sections for run detail responses.

Called by GET /api/runs/{run_id}?include=... to attach computed sections
(eval_heatmap, judge_quality, rankings, llm_calls, timeline) to the response.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.run_analytics import (
    build_eval_heatmap_section,
    build_judge_quality_section,
    build_rankings_section,
)
from app.services.results_reader import ResultsReader
from app.api.routes.runs.detail_payload import (
    build_combined_doc_ids,
    build_generated_docs,
    build_pairwise_results,
    build_post_combine_evals,
    build_post_combine_evals_detailed,
    build_pre_combine_evals_detailed,
    build_source_doc_results,
    build_task_summaries,
    build_timeline_events,
)
from app.api.routes.runs.helpers import _parse_metadata_json

logger = logging.getLogger(__name__)

_LOW_MEM_AVAILABLE_KB = int(os.environ.get("API_COST_X_RUN_DETAIL_LOW_MEM_AVAILABLE_KB", "524288"))
_MAX_TIMELINE_ROWS = int(os.environ.get("API_COST_X_RUN_DETAIL_MAX_TIMELINE_ROWS", "2000"))
_MAX_EVAL_SCORE_ROWS = int(os.environ.get("API_COST_X_RUN_DETAIL_MAX_EVAL_SCORE_ROWS", "4000"))
_MAX_PAIRWISE_ROWS = int(os.environ.get("API_COST_X_RUN_DETAIL_MAX_PAIRWISE_ROWS", "2000"))
_MAX_SOURCE_DOC_ROWS = int(os.environ.get("API_COST_X_RUN_DETAIL_MAX_SOURCE_DOC_ROWS", "1000"))


def _parse_requested_sections(include: str) -> set[str]:
    requested = {part.strip() for part in include.split(",") if part.strip()}
    if "terminal_summary" in requested:
        requested |= {
            "winner",
            "source_doc_overview",
        }
        requested.discard("terminal_summary")
    requested.discard("all_detail")
    return requested


def _snapshot_datasets_for_sections(requested_sections: set[str]) -> set[str]:
    datasets: set[str] = set()
    if requested_sections & {
        "generated_docs",
        "rankings",
        "source_doc_results",
        "source_doc_overview",
        "eval_heatmap",
        "judge_quality",
    }:
        datasets.add("generated_docs")
    if requested_sections & {"eval_heatmap", "judge_quality", "rankings", "source_doc_overview"}:
        datasets.add("eval_aggregates")
    if requested_sections & {
        "judge_quality",
        "post_combine_evals",
        "pre_combine_evals_detailed",
        "post_combine_evals_detailed",
        "source_doc_results",
    }:
        datasets.add("eval_scores_raw")
    if requested_sections & {"judge_quality", "pairwise_results", "post_combine_pairwise", "rankings", "source_doc_results", "source_doc_overview"}:
        datasets.add("pairwise_results")
    if requested_sections & {"timeline", "timeline_events", "source_doc_results"}:
        datasets.add("timeline_events")
    if requested_sections & {
        "combined_doc_ids",
        "post_combine_evals",
        "post_combine_evals_detailed",
        "source_doc_results",
        "source_doc_overview",
        "judge_quality",
    }:
        datasets.add("combined_docs")
    if requested_sections & {"source_doc_results", "source_doc_overview"}:
        datasets.add("source_doc_statuses")
    if requested_sections & {"winner", "eval_deviations", "source_doc_results"}:
        datasets.add("metadata")
    if requested_sections & {"judge_quality", "criteria_list"}:
        datasets.add("criteria_list")
    if requested_sections & {"judge_quality", "evaluator_list"}:
        datasets.add("evaluator_list")
    return datasets


def _read_mem_available_kb() -> Optional[int]:
    if not sys.platform.startswith("linux"):
        return None
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1])
    except Exception:
        return None
    return None


def _apply_low_memory_pruning(requested_sections: set[str], run_id: str) -> set[str]:
    mem_available_kb = _read_mem_available_kb()
    if mem_available_kb is None or mem_available_kb >= _LOW_MEM_AVAILABLE_KB:
        return requested_sections

    skipped = requested_sections & {
        "timeline_events",
        "pre_combine_evals_detailed",
        "post_combine_evals_detailed",
        "source_doc_results",
    }
    if not skipped:
        return requested_sections

    logger.warning(
        "[RUN DETAIL] Low-memory degraded mode for run %s; mem_available_kb=%d skipped=%s",
        run_id[:8],
        mem_available_kb,
        ",".join(sorted(skipped)),
    )
    return requested_sections - skipped


def _apply_snapshot_size_pruning(
    requested_sections: set[str],
    snapshot: Any,
    run_id: str,
) -> set[str]:
    skipped: set[str] = set()

    if len(snapshot.timeline_events or []) > _MAX_TIMELINE_ROWS:
        skipped |= requested_sections & {"timeline_events", "source_doc_results"}

    if len(snapshot.eval_scores_raw or []) > _MAX_EVAL_SCORE_ROWS:
        skipped |= requested_sections & {
            "pre_combine_evals_detailed",
            "post_combine_evals_detailed",
            "source_doc_results",
        }

    if len(snapshot.pairwise_results or []) > _MAX_PAIRWISE_ROWS:
        skipped |= requested_sections & {
            "pairwise_results",
            "post_combine_pairwise",
            "rankings",
            "source_doc_results",
            "source_doc_overview",
        }

    source_doc_rows = len(snapshot.source_doc_statuses or [])
    if source_doc_rows > _MAX_SOURCE_DOC_ROWS:
        skipped |= requested_sections & {"source_doc_results", "source_doc_overview"}

    if skipped:
        logger.warning(
            "[RUN DETAIL] Snapshot-size guard pruned sections for run %s; "
            "timeline_rows=%d eval_rows=%d pairwise_rows=%d source_doc_rows=%d skipped=%s",
            run_id[:8],
            len(snapshot.timeline_events or []),
            len(snapshot.eval_scores_raw or []),
            len(snapshot.pairwise_results or []),
            source_doc_rows,
            ",".join(sorted(skipped)),
        )
        return requested_sections - skipped

    return requested_sections


async def compute_sections(
    include: str,
    source_doc_id: Optional[str],
    db: AsyncSession,
    user: Dict[str, Any],
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute only the explicitly requested run-detail sections."""
    requested_sections = _apply_low_memory_pruning(_parse_requested_sections(include), run_id or "")
    if not requested_sections or not run_id:
        return {}

    snapshot = None
    snapshot_datasets = _snapshot_datasets_for_sections(requested_sections)
    if snapshot_datasets:
        reader = ResultsReader(db)
        snapshot = await reader.get_run_snapshot_for_datasets(
            run_id,
            datasets=snapshot_datasets,
            source_doc_id=source_doc_id,
        )
        requested_sections = _apply_snapshot_size_pruning(requested_sections, snapshot, run_id)
        if not requested_sections:
            return {}

    result: Dict[str, Any] = {}

    if snapshot is not None and "winner" in requested_sections:
        result["winner"] = snapshot.metadata.get("winner")
    if snapshot is not None and "generated_docs" in requested_sections:
        result["generated_docs"] = build_generated_docs(snapshot)
    if snapshot is not None and "post_combine_evals" in requested_sections:
        result["post_combine_evals"] = build_post_combine_evals(snapshot)
    if snapshot is not None and "pairwise_results" in requested_sections:
        result["pairwise_results"] = build_pairwise_results(snapshot, "pre_combine")
    if snapshot is not None and "post_combine_pairwise" in requested_sections:
        result["post_combine_pairwise"] = build_pairwise_results(snapshot, "post_combine")
    if snapshot is not None and "combined_doc_ids" in requested_sections:
        result["combined_doc_ids"] = build_combined_doc_ids(snapshot)
    if snapshot is not None and "pre_combine_evals_detailed" in requested_sections:
        result["pre_combine_evals_detailed"] = build_pre_combine_evals_detailed(snapshot)
    if snapshot is not None and "post_combine_evals_detailed" in requested_sections:
        result["post_combine_evals_detailed"] = build_post_combine_evals_detailed(snapshot)
    if snapshot is not None and "eval_deviations" in requested_sections:
        result["eval_deviations"] = _parse_metadata_json(snapshot.metadata, "eval_deviations") or {}
    if snapshot is not None and "criteria_list" in requested_sections:
        result["criteria_list"] = snapshot.criteria_list or []
    if snapshot is not None and "evaluator_list" in requested_sections:
        result["evaluator_list"] = snapshot.evaluator_list or []
    if snapshot is not None and "timeline_events" in requested_sections:
        result["timeline_events"] = build_timeline_events(snapshot)
    if snapshot is not None and "source_doc_results" in requested_sections:
        result["source_doc_results"] = build_source_doc_results(
            snapshot,
            source_doc_id=source_doc_id,
        )
    elif snapshot is not None and "source_doc_overview" in requested_sections:
        result["source_doc_results"] = build_source_doc_results(
            snapshot,
            source_doc_id=source_doc_id,
            include_single_eval_detailed=False,
            include_timeline_events=False,
            include_eval_deviations=False,
            include_pairwise_results=True,
            include_pairwise_comparisons=False,
            include_generated_docs=True,
            include_combined_docs=False,
            include_score_summaries=False,
        )

    if "tasks" in requested_sections:
        from app.infra.db.repositories import RunRepository

        repo = RunRepository(db, user_uuid=user["uuid"])
        run_with_tasks = await repo.get_with_tasks(run_id)
        result["tasks"] = build_task_summaries(list(run_with_tasks.tasks or [])) if run_with_tasks else []

    if snapshot is not None and "eval_heatmap" in requested_sections:
        eval_heatmap = build_eval_heatmap_section(snapshot)
        if eval_heatmap is not None:
            result["eval_heatmap"] = eval_heatmap

    if snapshot is not None and "judge_quality" in requested_sections:
        judge_quality = build_judge_quality_section(snapshot)
        if judge_quality is not None:
            result["judge_quality"] = judge_quality

    if snapshot is not None and "rankings" in requested_sections and snapshot.pairwise_results:
        result["rankings"] = build_rankings_section(
            snapshot.pairwise_results or [],
            list(snapshot.eval_aggregates or []),
            snapshot.generated_docs or [],
            "pre_combine",
        )

    if "llm_calls" in requested_sections:
        result["llm_calls"] = {
            "_meta": {
                "call_count": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_tokens": 0,
                "total_thinking_tokens": 0,
            },
            "calls": [],
        }

    if snapshot is not None and "timeline" in requested_sections:
        result["timeline"] = {
            "events": [
                {
                    "phase": row.get("phase", ""),
                    "event_type": row.get("event_type", ""),
                    "description": row.get("description"),
                    "model": row.get("model"),
                    "timestamp": row["occurred_at"].isoformat() if row.get("occurred_at") else None,
                    "duration_seconds": row.get("duration_seconds"),
                    "success": row.get("success", True),
                    "source_doc_id": row.get("source_doc_id"),
                }
                for row in (snapshot.timeline_events or [])
            ]
        }

    return result
