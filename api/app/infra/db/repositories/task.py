"""
Task repository for CRUD operations on tasks.
"""
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.infra.db.models.task import Task, TaskStatus
from app.infra.db.repositories.base import BaseRepository


class TaskRepository(BaseRepository[Task]):
    """Repository for Task CRUD operations."""

    def __init__(self, session: AsyncSession):
        super().__init__(Task, session)

    async def get_by_run(self, run_id: str) -> Sequence[Task]:
        """Get all tasks for a specific run."""
        stmt = (
            select(Task)
            .where(Task.run_id == run_id)
            .order_by(Task.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_with_artifacts(self, id: str) -> Optional[Task]:
        """Get a task with its artifacts eagerly loaded."""
        stmt = (
            select(Task)
            .options(selectinload(Task.artifacts))
            .where(Task.id == id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_pending_for_run(self, run_id: str) -> Sequence[Task]:
        """Get all pending tasks for a run."""
        stmt = (
            select(Task)
            .where(Task.run_id == run_id)
            .where(Task.status == TaskStatus.PENDING.value)
            .order_by(Task.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def start(self, id: str) -> Optional[Task]:
        """Mark a task as started."""
        task = await self.get_by_id(id)
        if task and task.status == TaskStatus.PENDING.value:
            task.status = TaskStatus.RUNNING.value
            task.started_at = datetime.utcnow()
            await self.session.commit()
            await self.session.refresh(task)
            return task
        return None

    async def complete(
        self,
        id: str,
        input_tokens: int = 0,
        output_tokens: int = 0
    ) -> Optional[Task]:
        """Mark a task as completed with token usage."""
        task = await self.get_by_id(id)
        if task:
            task.status = TaskStatus.COMPLETED.value
            task.completed_at = datetime.utcnow()
            task.progress = 100
            task.input_tokens = input_tokens
            task.output_tokens = output_tokens
            if task.started_at:
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
            await self.session.commit()
            await self.session.refresh(task)
            return task
        return None

    async def complete_with_output(
        self,
        id: str,
        output_ref: str,
    ) -> Optional[Task]:
        """Mark a generation/eval task as completed, storing the output file reference."""
        task = await self.get_by_id(id)
        if task:
            task.status = TaskStatus.COMPLETED.value
            task.completed_at = datetime.utcnow()
            task.progress = 100
            task.output_ref = output_ref
            if task.started_at:
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
            await self.session.commit()
            await self.session.refresh(task)
            return task
        return None

    async def fail(self, id: str, error_message: str) -> Optional[Task]:
        """Mark a task as failed."""
        task = await self.get_by_id(id)
        if task:
            task.status = TaskStatus.FAILED.value
            task.completed_at = datetime.utcnow()
            task.error_message = error_message
            if task.started_at:
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
            await self.session.commit()
            await self.session.refresh(task)
            return task
        return None

    async def cancel(self, id: str) -> Optional[Task]:
        """Cancel a task."""
        task = await self.get_by_id(id)
        if task and task.status in [TaskStatus.PENDING.value, TaskStatus.RUNNING.value]:
            task.status = TaskStatus.CANCELLED.value
            task.completed_at = datetime.utcnow()
            await self.session.commit()
            await self.session.refresh(task)
            return task
        return None

    async def update_progress(self, id: str, progress: int) -> Optional[Task]:
        """Update the progress percentage of a task."""
        task = await self.get_by_id(id)
        if task:
            task.progress = max(0, min(100, progress))
            await self.session.commit()
            await self.session.refresh(task)
            return task
        return None

    async def count_by_status(self, run_id: str, status: TaskStatus) -> int:
        """Count tasks with a specific status for a run."""
        stmt = (
            select(func.count())
            .select_from(Task)
            .where(Task.run_id == run_id)
            .where(Task.status == status.value)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def get_status_counts_for_run(self, run_id: str) -> Dict[str, int]:
        """Return per-status task counts for a run."""
        stmt = (
            select(Task.status, func.count())
            .where(Task.run_id == run_id)
            .group_by(Task.status)
        )
        result = await self.session.execute(stmt)
        counts: Dict[str, int] = {}
        for status, count in result.all():
            counts[str(status)] = int(count or 0)
        return counts

    def _empty_checkpoint_counts(self) -> Dict[str, int]:
        """Return the canonical count shape expected by resume APIs."""
        return {
            "total": 0,
            TaskStatus.PENDING.value: 0,
            TaskStatus.RUNNING.value: 0,
            TaskStatus.COMPLETED.value: 0,
            TaskStatus.FAILED.value: 0,
            TaskStatus.CANCELLED.value: 0,
            TaskStatus.SKIPPED.value: 0,
        }

    def _checkpoint_phase(self, phase: Optional[str]) -> str:
        """Normalize missing legacy phase values into the generation bucket."""
        normalized = str(phase or "").strip()
        aliases = {
            "evaluation": "single_eval",
            "combination": "combine",
            "post_combine_pairwise": "pairwise",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in {"generation", "single_eval", "pairwise", "combine"}:
            return normalized
        return "generation"

    async def get_checkpoint_summary(self, run_id: str) -> Dict[str, Dict[str, int]]:
        """Return per-phase and aggregate task counts for run resume/checkpoint APIs."""
        phases = ("generation", "single_eval", "pairwise", "combine", "all")
        summary: Dict[str, Dict[str, int]] = {
            phase: self._empty_checkpoint_counts() for phase in phases
        }

        stmt = (
            select(Task.phase, Task.status, func.count())
            .where(Task.run_id == run_id)
            .group_by(Task.phase, Task.status)
        )
        result = await self.session.execute(stmt)

        for phase, status, count in result.all():
            status_key = str(status or "")
            if status_key not in summary["all"]:
                continue
            amount = int(count or 0)
            bucket = self._checkpoint_phase(phase)
            summary[bucket]["total"] += amount
            summary[bucket][status_key] += amount
            summary["all"]["total"] += amount
            summary["all"][status_key] += amount

        return summary

    async def get_tasks_by_phase(self, run_id: str, phase: str) -> Sequence[Task]:
        """Return tasks for a run phase, ordered by creation time."""
        phase_filter = Task.phase == phase
        if phase == "generation":
            phase_filter = or_(Task.phase == phase, Task.phase.is_(None))
        stmt = (
            select(Task)
            .where(Task.run_id == run_id)
            .where(phase_filter)
            .order_by(Task.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def find_completed_generation_task(
        self,
        run_id: str,
        source_doc_id: str,
        model_name: str,
        iteration: int,
    ) -> Optional[Task]:
        """Find a completed generation checkpoint for a source document/model/iteration."""
        return await self.find_completed_phase_task(
            run_id,
            source_doc_id,
            phase="generation",
            model_name=model_name,
            iteration=iteration,
        )

    async def create_generation_task(
        self,
        *,
        run_id: str,
        source_doc_id: str,
        model_name: str,
        generator: str,
        iteration: int = 1,
    ) -> Task:
        """Create a pending generation checkpoint task."""
        return await self.create(
            run_id=run_id,
            document_id=source_doc_id,
            model_name=model_name,
            iteration=iteration,
            phase="generation",
            generator=generator,
            status=TaskStatus.PENDING.value,
            progress=0,
        )

    async def create_eval_task(
        self,
        *,
        run_id: str,
        source_doc_id: str,
        judge_model: str,
        iteration: int = 1,
        phase: str = "single_eval",
    ) -> Task:
        """Create a pending evaluation checkpoint task."""
        return await self.create(
            run_id=run_id,
            document_id=source_doc_id,
            model_name=judge_model,
            iteration=iteration,
            phase=phase,
            judge_model=judge_model,
            status=TaskStatus.PENDING.value,
            progress=0,
        )

    async def find_completed_phase_task(
        self,
        run_id: str,
        source_doc_id: str,
        *,
        phase: str,
        model_name: str,
        iteration: int = 1,
    ) -> Optional[Task]:
        """Find a completed durable checkpoint task for an arbitrary phase."""
        stmt = (
            select(Task)
            .where(Task.run_id == run_id)
            .where(Task.document_id == source_doc_id)
            .where(Task.phase == phase)
            .where(Task.model_name == model_name)
            .where(Task.iteration == iteration)
            .where(Task.status == TaskStatus.COMPLETED.value)
            .order_by(Task.completed_at.desc(), Task.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_phase_task(
        self,
        *,
        run_id: str,
        source_doc_id: str,
        model_name: str,
        phase: str,
        iteration: int = 1,
        generator: Optional[str] = None,
        judge_model: Optional[str] = None,
    ) -> Task:
        """Create a pending checkpoint task for pairwise/combine or other durable phases."""
        return await self.create(
            run_id=run_id,
            document_id=source_doc_id,
            model_name=model_name,
            iteration=iteration,
            phase=phase,
            generator=generator,
            judge_model=judge_model,
            status=TaskStatus.PENDING.value,
            progress=0,
        )

    async def delete_phase_tasks(
        self,
        run_id: str,
        source_doc_id: str,
        *,
        phase: str,
        model_name: Optional[str] = None,
        iteration: Optional[int] = None,
    ) -> int:
        """Delete checkpoint tasks for a phase and return the number of deleted rows."""
        stmt = (
            delete(Task)
            .where(Task.run_id == run_id)
            .where(Task.document_id == source_doc_id)
            .where(Task.phase == phase)
        )
        if model_name is not None:
            stmt = stmt.where(Task.model_name == model_name)
        if iteration is not None:
            stmt = stmt.where(Task.iteration == iteration)
        result = await self.session.execute(stmt)
        await self.session.commit()
        return int(result.rowcount or 0)

    async def reset_stale_running(self, run_id: str) -> int:
        """Reset running tasks to pending so an interrupted run can resume safely."""
        stmt = (
            update(Task)
            .where(Task.run_id == run_id)
            .where(Task.status == TaskStatus.RUNNING.value)
            .values(status=TaskStatus.PENDING.value, started_at=None, progress=0)
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return int(result.rowcount or 0)
