import os

os.environ["DEBUG"] = "false"

from app.api.schemas.runs import (
    RunProgress,
    RunRealtimeUpdate,
    RunStatus,
    RunUpdate,
)


def test_run_update_is_request_dto_after_deduplication():
    assert set(RunUpdate.model_fields) == {"name", "description", "tags"}


def test_run_realtime_update_has_websocket_shape():
    assert set(RunRealtimeUpdate.model_fields) == {
        "run_id",
        "event",
        "status",
        "progress",
        "timestamp",
    }

    payload = RunRealtimeUpdate(
        run_id="run-123",
        event="started",
        status=RunStatus.RUNNING,
        progress=RunProgress(
            total_tasks=10,
            completed_tasks=2,
            running_tasks=1,
            failed_tasks=0,
            pending_tasks=7,
            progress_percent=20,
            estimated_remaining_seconds=42.0,
        ),
    )

    assert payload.run_id == "run-123"
    assert payload.status is RunStatus.RUNNING
