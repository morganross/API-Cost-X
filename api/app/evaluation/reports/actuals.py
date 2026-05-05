from typing import List, Dict, Any, Optional
from datetime import datetime
from .models import TimelineRow, TimelinePhase, TimelineStatus

def collect_actuals(run_data: Dict[str, Any], expected_rows: List[TimelineRow]) -> List[TimelineRow]:
    """
    Merge actual execution data into the expected timeline rows.

    Args:
        run_data: The run dictionary (from RunStore or DB).
        expected_rows: The list of expected rows generated from config.

    Returns:
        Updated list of TimelineRows with actuals filled in.
    """
    tasks = run_data.get("tasks", [])

    # Index tasks by a key for easy lookup
    # Key format: "{doc_id}.{generator}.{iteration}.{model}"
    # Note: model in task_id might be just the model name, not provider:model
    task_map = {}
    for t in tasks:
        # t is a dict in the in-memory store
        t_id = t.get("id")
        if t_id:
            task_map[t_id] = t

    # Iterate over expected rows and try to find matching actuals
    for row in expected_rows:
        if row.phase == TimelinePhase.GENERATION:
            # Reconstruct the task ID from the row target
            # Target format: "{doc_id} (Iter {i})"
            # We need to parse this back or pass more info in expected rows
            # This is fragile. Better to store metadata in TimelineRow.

            # Let's try to match by iterating tasks and checking properties
            # row.judge_model is "provider:model"
            # row.target is "doc_id (Iter i)"

            # Parse target
            try:
                doc_part, iter_part = row.target.rsplit(" (Iter ", 1)
                doc_id = doc_part
                iteration = int(iter_part.rstrip(")"))
            except ValueError:
                continue

            # Parse model
            if ":" in row.judge_model:
                provider, model_name = row.judge_model.split(":", 1)
            else:
                model_name = row.judge_model

            # Find matching task
            matched_task = None
            for t in tasks:
                # Check doc_id
                if t.get("document_id") != doc_id:
                    continue
                # Check iteration
                if t.get("iteration") != iteration:
                    continue
                # Check model (fuzzy match because of provider)
                t_model = t.get("model")
                if t_model != model_name and t_model != row.judge_model:
                    continue

                matched_task = t
                break

            if matched_task:
                row.started_at = _parse_time(matched_task.get("started_at"))
                row.finished_at = _parse_time(matched_task.get("completed_at"))
                row.duration_seconds = matched_task.get("duration_seconds")
                # Tokens not always tracked in task dict, check if available
                row.tokens = matched_task.get("total_tokens") # or input+output

                status_str = matched_task.get("status", "pending")
                row.status = _map_status(status_str)
                row.source_used = "run_store"
                row.actual_target = matched_task.get("id")

        elif row.phase == TimelinePhase.PRECOMBINE_SINGLE:
            # Single eval actuals: results_summary blob removed (Phase 7).
            # single_eval_results would come from normalized run_eval_scores table.
            # This branch is a graceful no-op until reporting reads from normalized tables.
            single_evals = {}

            # Parse target to get doc_id
            # Target: "Eval: {doc_id} / {model_name} / {i}"
            try:
                parts = row.target.split(" / ")
                if len(parts) == 3:
                    doc_part = parts[0].replace("Eval: ", "")
                    # model_part = parts[1]
                    # iter_part = parts[2]

                    # Check if we have a result for this doc
                    if doc_part in single_evals:
                        # We have a result!
                        # But we don't have granular timing for the eval itself in the summary
                        # We only know it happened.
                        row.status = TimelineStatus.SUCCESS
                        row.source_used = "run_summary"
                        # We could estimate duration if granular timing were available
            except Exception:
                pass

    return expected_rows

def _parse_time(t: Any) -> Optional[datetime]:
    if isinstance(t, datetime):
        return t
    if isinstance(t, str):
        try:
            return datetime.fromisoformat(t)
        except ValueError:
            pass
    return None

def _map_status(s: str) -> TimelineStatus:
    s = s.lower()
    if s == "completed":
        return TimelineStatus.SUCCESS
    if s == "failed":
        return TimelineStatus.MISSING # or partial?
    if s == "running":
        return TimelineStatus.RUNNING
    if s == "pending":
        return TimelineStatus.PENDING
    return TimelineStatus.MISSING
