import hashlib
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks

from app.api.routes.runs import execution
from app.infra.db.repositories.run import RunRepository
from app.services import run_resumability
from app.services.run_resumability import classify_run_resumability


def _checkpoint_summary(**overrides):
    base = {
        "generation": {"total": 0, "pending": 0, "running": 0, "completed": 0, "failed": 0, "cancelled": 0, "skipped": 0},
        "single_eval": {"total": 0, "pending": 0, "running": 0, "completed": 0, "failed": 0, "cancelled": 0, "skipped": 0},
        "pairwise": {"total": 0, "pending": 0, "running": 0, "completed": 0, "failed": 0, "cancelled": 0, "skipped": 0},
        "combine": {"total": 0, "pending": 0, "running": 0, "completed": 0, "failed": 0, "cancelled": 0, "skipped": 0},
        "all": {"total": 0, "pending": 0, "running": 0, "completed": 0, "failed": 0, "cancelled": 0, "skipped": 0},
    }
    for phase, values in overrides.items():
        base[phase].update(values)
    return base


def _compiled_run_config(**overrides):
    payload = {
        "document_ids": ["doc-a"],
        "document_contents": {"doc-a": "Document A"},
        "generators": ["fpf"],
        "models": ["openai:gpt-4o"],
        "model_settings": {"openai:gpt-4o": {"temperature": 0.7, "max_tokens": 32000}},
        "instructions": "Generate a useful answer.",
    }
    payload.update(overrides)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return {
        "compiled_config": {
            "version": 1,
            "built_at": "2026-04-21T00:00:00+00:00",
            "hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "config": payload,
        }
    }


def test_classify_run_resumability_paused_run_is_resumable():
    run = SimpleNamespace(
        id="run-1",
        status="paused",
        preset_id="preset-1",
        started_at=datetime.utcnow(),
        completed_at=None,
    )
    info = classify_run_resumability(run, _checkpoint_summary())

    assert info["resumable"] is True
    assert info["resume_mode"] == "paused"
    assert "continue from its existing checkpoints" in info["reason"]


def test_classify_run_resumability_interrupted_active_run_is_resumable_without_executor():
    run = SimpleNamespace(
        id="run-2",
        status="running",
        preset_id="preset-1",
        started_at=datetime.utcnow(),
        completed_at=None,
    )
    info = classify_run_resumability(
        run,
        _checkpoint_summary(
            generation={"total": 4, "completed": 2, "running": 1, "pending": 1},
            all={"total": 4, "completed": 2, "running": 1, "pending": 1},
        ),
        active_executor_present=False,
    )

    assert info["resumable"] is True
    assert info["resume_mode"] == "interrupted"
    assert info["stale_running_tasks"] == 1
    assert info["reusable_generation_tasks"] == 2


def test_classify_run_resumability_running_with_active_executor_is_not_resumable():
    run = SimpleNamespace(
        id="run-3",
        status="running",
        preset_id="preset-1",
        started_at=datetime.utcnow(),
        completed_at=None,
    )
    info = classify_run_resumability(
        run,
        _checkpoint_summary(all={"total": 1, "running": 1}),
        active_executor_present=True,
    )

    assert info["resumable"] is False
    assert info["resume_mode"] == "not_resumable"
    assert any("live executor" in msg.lower() for msg in info["blocking_errors"])


def test_classify_run_resumability_terminal_incomplete_completed_run_is_resumable():
    run = SimpleNamespace(
        id="run-3b",
        status="completed",
        preset_id="preset-1",
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
    )
    info = classify_run_resumability(
        run,
        _checkpoint_summary(),
        terminal_incomplete=True,
        terminal_incomplete_reason="Run is missing 1 evaluation attempt.",
    )

    assert info["resumable"] is True
    assert info["resume_mode"] == "terminal_incomplete"
    assert "missing 1 evaluation attempt" in info["reason"]


def test_build_completed_eval_cache_requires_full_criteria_coverage_per_trial():
    cache = execution._build_completed_eval_cache(
        [
            {"doc_id": "doc-a", "judge_model": "judge-a", "trial": 1, "criterion": "clarity"},
            {"doc_id": "doc-a", "judge_model": "judge-a", "trial": 1, "criterion": "accuracy"},
            {"doc_id": "doc-a", "judge_model": "judge-a", "trial": 2, "criterion": "clarity"},
        ],
        expected_criteria_count=2,
    )

    assert cache == {"doc-a": {("judge-a", 1)}}


@pytest.mark.asyncio
async def test_run_repository_resume_clears_terminal_markers_for_interrupted_run():
    run = SimpleNamespace(
        status="running",
        pause_requested=1,
        resume_count=2,
        completed_at=datetime.utcnow(),
        error_message="old failure",
        started_at=None,
    )

    repo = object.__new__(RunRepository)
    repo.get_by_id = lambda _id: None  # type: ignore[attr-defined]
    async def _get_by_id(_id):
        return run
    repo.get_by_id = _get_by_id  # type: ignore[assignment]
    repo.session = SimpleNamespace(
        commit=lambda: None,
        refresh=lambda _run: None,
    )

    async def _commit():
        return None

    async def _refresh(_run):
        return None

    repo.session.commit = _commit
    repo.session.refresh = _refresh

    resumed = await RunRepository.resume(repo, "run-4", allow_interrupted=True)

    assert resumed is run
    assert run.status == "running"
    assert run.pause_requested == 0
    assert run.resume_count == 3
    assert run.completed_at is None
    assert run.error_message is None
    assert run.started_at is not None


@pytest.mark.asyncio
async def test_run_repository_resume_allows_terminal_incomplete_terminal_status():
    run = SimpleNamespace(
        status="completed_with_errors",
        pause_requested=1,
        resume_count=2,
        completed_at=datetime.utcnow(),
        error_message="partial failure",
        started_at=None,
    )

    repo = object.__new__(RunRepository)

    async def _get_by_id(_id):
        return run

    repo.get_by_id = _get_by_id  # type: ignore[assignment]
    repo.session = SimpleNamespace(
        commit=lambda: None,
        refresh=lambda _run: None,
    )

    async def _commit():
        return None

    async def _refresh(_run):
        return None

    repo.session.commit = _commit
    repo.session.refresh = _refresh

    resumed = await RunRepository.resume(repo, "run-4", allow_terminal_incomplete=True)

    assert resumed is run
    assert run.status == "running"
    assert run.pause_requested == 0
    assert run.resume_count == 3
    assert run.completed_at is None
    assert run.error_message is None
    assert run.started_at is not None


@pytest.mark.asyncio
async def test_resume_run_accepts_interrupted_mode(monkeypatch):
    recorded = {}

    class _FakeRunRepo:
        def __init__(self, db, user_uuid):
            self.db = db
            self.user_uuid = user_uuid

        async def get_by_id(self, run_id):
            return SimpleNamespace(
                id=run_id,
                status=execution.RunStatus.RUNNING,
                preset_id="preset-1",
                started_at=datetime.utcnow(),
                completed_at=None,
                pause_requested=0,
                resume_count=0,
            )

        async def resume(self, run_id, *, allow_interrupted=False, allow_terminal_incomplete=False):
            recorded["allow_interrupted"] = allow_interrupted
            recorded["allow_terminal_incomplete"] = allow_terminal_incomplete
            return SimpleNamespace(resume_count=1)

    class _FakePresetRepo:
        def __init__(self, db, user_uuid):
            self.db = db
            self.user_uuid = user_uuid

        async def get_by_id(self, preset_id):
            return SimpleNamespace(id=preset_id)

    class _FakeTaskRepo:
        def __init__(self, db):
            self.db = db

        async def reset_stale_running(self, run_id):
            return 1

        async def get_tasks_by_phase(self, run_id, phase):
            return []

    async def _fake_build_executor_config(*, run_id, run, user, db):
        return SimpleNamespace(completed_generation_cache={}), {}

    async def _fake_build_run_resume_info(*, db, run, active_executor_present=False):
        return {
            "run_id": run.id,
            "run_status": "running",
            "resumable": True,
            "resume_mode": "interrupted",
            "reason": "interrupted",
            "has_active_executor": False,
            "requires_preset": True,
            "phase_hint": "generation",
            "stale_running_tasks": 1,
            "reusable_generation_tasks": 1,
            "reusable_eval_tasks": 0,
            "reusable_pairwise_tasks": 0,
            "reusable_combine_tasks": 0,
            "checkpoint_summary": _checkpoint_summary(all={"total": 1, "running": 1}),
            "warnings": [],
            "blocking_errors": [],
        }

    monkeypatch.setattr(execution, "RunRepository", _FakeRunRepo)
    monkeypatch.setattr(execution, "TaskRepository", _FakeTaskRepo)
    monkeypatch.setattr(execution, "build_run_resume_info", _fake_build_run_resume_info)
    monkeypatch.setattr(execution, "_build_executor_config", _fake_build_executor_config)
    monkeypatch.setattr(execution, "_active_executors", {})

    import app.infra.db.repositories as repo_module
    monkeypatch.setattr(repo_module, "PresetRepository", _FakePresetRepo)

    background_tasks = BackgroundTasks()
    response = await execution.resume_run(
        run_id="run-5",
        background_tasks=background_tasks,
        user={"uuid": "user-1"},
        db=SimpleNamespace(),
    )

    assert response["status"] == "running"
    assert response["resume_mode"] == "interrupted"
    assert recorded["allow_interrupted"] is True
    assert recorded["allow_terminal_incomplete"] is False


@pytest.mark.asyncio
async def test_resume_run_accepts_terminal_incomplete_mode(monkeypatch):
    recorded = {}

    class _FakeRunRepo:
        def __init__(self, db, user_uuid):
            self.db = db
            self.user_uuid = user_uuid

        async def get_by_id(self, run_id):
            return SimpleNamespace(
                id=run_id,
                status=execution.RunStatus.COMPLETED_WITH_ERRORS,
                preset_id="preset-1",
                started_at=datetime.utcnow(),
                completed_at=datetime.utcnow(),
                pause_requested=0,
                resume_count=1,
            )

        async def resume(self, run_id, *, allow_interrupted=False, allow_terminal_incomplete=False):
            recorded["allow_interrupted"] = allow_interrupted
            recorded["allow_terminal_incomplete"] = allow_terminal_incomplete
            return SimpleNamespace(resume_count=2)

    class _FakePresetRepo:
        def __init__(self, db, user_uuid):
            self.db = db
            self.user_uuid = user_uuid

        async def get_by_id(self, preset_id):
            return SimpleNamespace(id=preset_id)

    class _FakeTaskRepo:
        def __init__(self, db):
            self.db = db

        async def reset_stale_running(self, run_id):
            return 0

        async def get_tasks_by_phase(self, run_id, phase):
            return []

    async def _fake_build_executor_config(*, run_id, run, user, db):
        return SimpleNamespace(completed_generation_cache={}), {}

    async def _fake_build_run_resume_info(*, db, run, active_executor_present=False):
        return {
            "run_id": run.id,
            "run_status": "completed_with_errors",
            "resumable": True,
            "resume_mode": "terminal_incomplete",
            "reason": "partial results",
            "has_active_executor": False,
            "requires_preset": True,
            "phase_hint": "single_eval",
            "stale_running_tasks": 0,
            "reusable_generation_tasks": 1,
            "reusable_eval_tasks": 3,
            "reusable_pairwise_tasks": 0,
            "reusable_combine_tasks": 0,
            "checkpoint_summary": _checkpoint_summary(all={"total": 4, "completed": 4}),
            "warnings": [],
            "blocking_errors": [],
        }

    monkeypatch.setattr(execution, "RunRepository", _FakeRunRepo)
    monkeypatch.setattr(execution, "TaskRepository", _FakeTaskRepo)
    monkeypatch.setattr(execution, "build_run_resume_info", _fake_build_run_resume_info)
    monkeypatch.setattr(execution, "_build_executor_config", _fake_build_executor_config)
    monkeypatch.setattr(execution, "_active_executors", {})

    import app.infra.db.repositories as repo_module
    monkeypatch.setattr(repo_module, "PresetRepository", _FakePresetRepo)

    background_tasks = BackgroundTasks()
    response = await execution.resume_run(
        run_id="run-5b",
        background_tasks=background_tasks,
        user={"uuid": "user-1"},
        db=SimpleNamespace(),
    )

    assert response["status"] == "running"
    assert response["resume_mode"] == "terminal_incomplete"
    assert recorded["allow_interrupted"] is False
    assert recorded["allow_terminal_incomplete"] is True


@pytest.mark.asyncio
async def test_build_run_resume_info_reports_split_pairwise_and_row_based_combine_reuse(
    monkeypatch,
    tmp_path,
):
    valid_file = tmp_path / "combined-valid.md"
    valid_file.write_text("combined content", encoding="utf-8")

    class _FakeTaskRepo:
        def __init__(self, db):
            self.db = db

        async def get_checkpoint_summary(self, run_id):
            assert run_id == "run-6"
            return _checkpoint_summary(
                pairwise={"total": 2, "completed": 2},
                combine={"total": 0, "completed": 0},
                all={"total": 2, "completed": 2},
            )

        async def get_tasks_by_phase(self, run_id, phase):
            assert run_id == "run-6"
            assert phase == "pairwise"
            return [
                SimpleNamespace(status="completed", model_name="pre_combine_summary", document_id="doc-a"),
                SimpleNamespace(status="completed", model_name="post_combine_summary", document_id="doc-b"),
            ]

    class _FakeRunResultsRepo:
        def __init__(self, db):
            self.db = db

        async def get_pairwise_results(
            self,
            run_id,
            source_doc_id=None,
            comparison_type=None,
            limit=None,
            offset=0,
        ):
            assert run_id == "run-6"
            if comparison_type == "pre_combine":
                return [
                    {
                        "source_doc_id": "doc-a",
                        "doc_id_a": "doc-a-1",
                        "doc_id_b": "doc-a-2",
                        "winner_doc_id": "doc-a-1",
                        "judge_model": "judge-a",
                    }
                ]
            if comparison_type == "post_combine":
                return [
                    {
                        "source_doc_id": "doc-b",
                        "doc_id_a": "doc-b-1",
                        "doc_id_b": "combined-b",
                        "winner_doc_id": "combined-b",
                        "judge_model": "judge-b",
                    }
                ]
            return []

        async def get_combined_docs(self, run_id, source_doc_id=None):
            assert run_id == "run-6"
            return [
                {
                    "source_doc_id": "doc-a",
                    "combine_model": "openai:gpt-4.1",
                    "input_doc_ids": "doc-a-1,doc-a-2",
                    "file_path": str(valid_file),
                    "completed_at": datetime(2026, 4, 19, 12, 0, 0),
                }
            ]

    monkeypatch.setattr(run_resumability, "TaskRepository", _FakeTaskRepo)
    monkeypatch.setattr(run_resumability, "RunResultsRepository", _FakeRunResultsRepo)

    info = await run_resumability.build_run_resume_info(
        db=object(),
        run=SimpleNamespace(
            id="run-6",
            status="running",
            preset_id="preset-1",
            started_at=datetime.utcnow(),
            completed_at=None,
            config=_compiled_run_config(),
        ),
    )

    assert info["reusable_pre_combine_pairwise_tasks"] == 1
    assert info["reusable_post_combine_pairwise_tasks"] == 1
    assert info["reusable_pairwise_tasks"] == 2
    assert info["reusable_combine_tasks"] == 1


@pytest.mark.asyncio
async def test_build_run_resume_info_marks_completed_run_resumable_when_eval_attempts_missing(monkeypatch):
    class _FakeTaskRepo:
        def __init__(self, db):
            self.db = db

        async def get_checkpoint_summary(self, run_id):
            return _checkpoint_summary(all={"total": 2, "completed": 2})

        async def get_tasks_by_phase(self, run_id, phase):
            return []

    class _FakeRunResultsRepo:
        def __init__(self, db):
            self.db = db

        async def get_source_doc_statuses(self, run_id, source_doc_id=None):
            return []

        async def get_generated_docs(self, run_id, source_doc_id=None):
            return [
                SimpleNamespace(doc_id="doc-a"),
                SimpleNamespace(doc_id="doc-b"),
            ]

        async def get_eval_scores(self, run_id, doc_id=None, source_doc_id=None, criterion=None, judge_model=None):
            return [
                {"doc_id": "doc-a", "judge_model": "judge-a", "trial": 1},
                {"doc_id": "doc-a", "judge_model": "judge-b", "trial": 1},
                {"doc_id": "doc-b", "judge_model": "judge-a", "trial": 1},
            ]

        async def get_pairwise_results(self, run_id, source_doc_id=None, comparison_type=None, limit=None, offset=0):
            return []

        async def get_combined_docs(self, run_id, source_doc_id=None):
            return []

    monkeypatch.setattr(run_resumability, "TaskRepository", _FakeTaskRepo)
    monkeypatch.setattr(run_resumability, "RunResultsRepository", _FakeRunResultsRepo)

    info = await run_resumability.build_run_resume_info(
        db=object(),
        run=SimpleNamespace(
            id="run-7",
            status="completed",
            preset_id="preset-1",
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
            config=_compiled_run_config(
                eval_config={
                    "judge_models": ["judge-a", "judge-b"],
                    "iterations": 1,
                },
                enable_single_eval=True,
            ),
        ),
    )

    assert info["resumable"] is True
    assert info["resume_mode"] == "terminal_incomplete"
    assert "evaluation attempt" in info["reason"]


@pytest.mark.asyncio
async def test_build_run_resume_info_rejects_runs_without_compiled_config(monkeypatch):
    class _FakeTaskRepo:
        def __init__(self, db):
            self.db = db

        async def get_checkpoint_summary(self, run_id):
            return _checkpoint_summary(
                generation={"total": 2, "completed": 1, "running": 1},
                all={"total": 2, "completed": 1, "running": 1},
            )

        async def get_tasks_by_phase(self, run_id, phase):
            return []

    monkeypatch.setattr(run_resumability, "TaskRepository", _FakeTaskRepo)

    info = await run_resumability.build_run_resume_info(
        db=object(),
        run=SimpleNamespace(
            id="run-8",
            status="running",
            preset_id="preset-1",
            started_at=datetime.utcnow(),
            completed_at=None,
            config={},
        ),
    )

    assert info["resumable"] is False
    assert info["resume_mode"] == "not_resumable"
    assert "missing compiled_config" in info["reason"]
