from typing import Dict, Any, List, Union
from app.infra.db.models.run import Run
from .models import TimelineChart, TimelinePhaseSummary, TimelinePhase
from .expected import build_expected_plan
from .actuals import collect_actuals

def generate_timeline_chart(run: Union[Run, Dict[str, Any]], run_data: Dict[str, Any]) -> TimelineChart:
    """
    Generate the full Evaluation Timeline Chart.

    Args:
        run: The Run model (for config).
        run_data: The runtime data (tasks, results).

    Returns:
        Populated TimelineChart.
    """
    # 1. Build Expected Plan
    rows = build_expected_plan(run, run_data)

    # 2. Collect Actuals
    rows = collect_actuals(run_data, rows)

    # 3. Calculate Summaries
    summaries = {}
    total_tokens = 0
    total_duration = 0.0 # This is tricky, sum of durations != wall clock time

    # Group by phase
    phase_groups = {}
    for row in rows:
        if row.phase not in phase_groups:
            phase_groups[row.phase] = []
        phase_groups[row.phase].append(row)

    for phase, p_rows in phase_groups.items():
        p_count = len(p_rows)
        p_dur = sum(r.duration_seconds or 0.0 for r in p_rows)
        p_tokens = sum(r.tokens or 0 for r in p_rows)

        summaries[phase.value] = TimelinePhaseSummary(
            phase=phase,
            count=p_count,
            total_duration=p_dur,
            total_tokens=p_tokens
        )

        total_tokens += p_tokens
        total_duration += p_dur # Sum of task durations

    return TimelineChart(
        rows=rows,
        summaries=summaries,
        total_tokens=total_tokens,
        total_duration=total_duration
    )
