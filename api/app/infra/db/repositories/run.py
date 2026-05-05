"""
Run repository for CRUD operations on runs.
"""
from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import select, func, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.infra.db.models.run import Run, RunStatus
from app.infra.db.repositories.base import BaseRepository


class RunRepository(BaseRepository[Run]):
    """Repository for Run CRUD operations."""

    def __init__(self, session: AsyncSession, user_uuid: Optional[str] = None):
        super().__init__(Run, session, user_uuid)

    async def get_by_preset(self, preset_id: str, limit: int = 100) -> Sequence[Run]:
        """Get all runs for a specific preset (scoped to user if user_uuid is set)."""
        stmt = (
            select(Run)
            .where(Run.preset_id == preset_id)
            .order_by(Run.created_at.desc())
            .limit(limit)
        )
        stmt = self._apply_user_filter(stmt)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_with_tasks(self, id: str) -> Optional[Run]:
        """Get a run with its tasks eagerly loaded (scoped to user if user_uuid is set)."""
        stmt = (
            select(Run)
            .options(selectinload(Run.tasks))
            .where(Run.id == id)
        )
        stmt = self._apply_user_filter(stmt)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_with_tasks(
        self,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None
    ) -> Sequence[Run]:
        """Get all runs with tasks eagerly loaded (scoped to user if user_uuid is set)."""
        stmt = select(Run).options(selectinload(Run.tasks))

        # Apply user filter
        stmt = self._apply_user_filter(stmt)

        # Apply status filter
        if status:
            stmt = stmt.where(Run.status == status)

        # Then order, offset, limit
        stmt = stmt.order_by(Run.created_at.desc()).offset(offset).limit(limit)

        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_all_for_list(
        self,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None
    ) -> Sequence[Run]:
        """Lean list query — does NOT load tasks. Safe because calculate_progress()
        falls back to run.total_tasks / completed_tasks / failed_tasks when tasks
        are not in the SQLAlchemy identity map. Use this for the history list endpoint
        to avoid loading hundreds of task rows per page."""
        stmt = select(Run)
        stmt = self._apply_user_filter(stmt)
        if status:
            stmt = stmt.where(Run.status == status)
        stmt = stmt.order_by(Run.created_at.desc()).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def search_by_title(
        self,
        query: str,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Run]:
        """Search runs by title or id for user-facing history and log browsing."""
        like_query = f"%{query}%"
        stmt = (
            select(Run)
            .where(or_(Run.title.ilike(like_query), Run.id.ilike(like_query)))
            .order_by(Run.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        stmt = self._apply_user_filter(stmt)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count_search_by_title(self, query: str) -> int:
        """Count runs matching a title/id search query."""
        like_query = f"%{query}%"
        stmt = (
            select(func.count())
            .select_from(Run)
            .where(or_(Run.title.ilike(like_query), Run.id.ilike(like_query)))
        )
        stmt = self._apply_user_filter(stmt)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def count(self, status: Optional[str] = None) -> int:
        """Return the total number of runs (optionally filtered by status, scoped to user if user_uuid is set)."""
        stmt = select(func.count()).select_from(Run)
        if self.user_uuid is not None:
            stmt = stmt.where(Run.user_uuid == self.user_uuid)
        if status:
            stmt = stmt.where(Run.status == status)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def bulk_delete_by_status(self, statuses: list[str]) -> int:
        """Delete all runs whose status is in the provided list (scoped to user if user_uuid is set)."""
        if not statuses:
            return 0
        stmt = delete(Run).where(Run.status.in_(statuses))
        if self.user_uuid is not None:
            stmt = stmt.where(Run.user_uuid == self.user_uuid)
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.rowcount or 0

    async def get_active_runs(self) -> Sequence[Run]:
        """Get all runs that are currently in progress (scoped to user if user_uuid is set)."""
        stmt = (
            select(Run)
            .where(Run.status.in_([RunStatus.PENDING.value, RunStatus.RUNNING.value]))
            .order_by(Run.created_at.asc())
        )
        stmt = self._apply_user_filter(stmt)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def start(self, id: str) -> Optional[Run]:
        """Mark a run as started."""
        run = await self.get_by_id(id)
        if run and run.status == RunStatus.PENDING.value:
            run.status = RunStatus.RUNNING.value
            run.started_at = datetime.utcnow()
            await self.session.commit()
            await self.session.refresh(run)
            return run
        return None

    async def complete(
        self,
        id: str,
    ) -> Optional[Run]:
        """Mark a run as completed."""
        run = await self.get_by_id(id)
        if run:
            run.status = RunStatus.COMPLETED.value
            run.completed_at = datetime.utcnow()
            run.completed_tasks = run.total_tasks  # All done
            await self.session.commit()
            await self.session.refresh(run)
            return run
        return None

    async def complete_with_errors(
        self,
        id: str,
        error_message: Optional[str] = None,
    ) -> Optional[Run]:
        """Mark a run as completed with errors."""
        run = await self.get_by_id(id)
        if run:
            run.status = RunStatus.COMPLETED_WITH_ERRORS.value
            run.completed_at = datetime.utcnow()
            run.completed_tasks = run.total_tasks  # Terminal state reached
            if error_message:
                run.error_message = error_message
            await self.session.commit()
            await self.session.refresh(run)
            return run
        return None

    async def fail(self, id: str, error_message: Optional[str] = None) -> Optional[Run]:
        """Mark a run as failed."""
        run = await self.get_by_id(id)
        if run:
            run.status = RunStatus.FAILED.value
            run.completed_at = datetime.utcnow()
            if error_message:
                run.error_message = error_message
            await self.session.commit()
            await self.session.refresh(run)
            return run
        return None

    async def cancel(self, id: str) -> Optional[Run]:
        """Cancel a run."""
        run = await self.get_by_id(id)
        if run and run.status in [RunStatus.PENDING.value, RunStatus.RUNNING.value, RunStatus.PAUSED.value]:
            run.status = RunStatus.CANCELLED.value
            run.completed_at = datetime.utcnow()
            await self.session.commit()
            await self.session.refresh(run)
            return run
        return None

    async def pause(self, id: str) -> Optional[Run]:
        """Pause a running run."""
        run = await self.get_by_id(id)
        if run and run.status == RunStatus.RUNNING.value:
            run.status = RunStatus.PAUSED.value
            await self.session.commit()
            await self.session.refresh(run)
            return run
        return None

    async def resume(
        self,
        id: str,
        *,
        allow_interrupted: bool = False,
        allow_terminal_incomplete: bool = False,
    ) -> Optional[Run]:
        """Resume a run, clearing terminal markers and bumping resume_count.

        By default only paused/failed runs are resumable. When
        ``allow_interrupted`` is true, stale active runs (running/pending with
        no live executor) may also be resumed by the caller. When
        ``allow_terminal_incomplete`` is true, terminal runs with missing
        durable work may also be resumed by the caller.
        """
        run = await self.get_by_id(id)
        allowed_statuses = [RunStatus.PAUSED.value, RunStatus.FAILED.value]
        if allow_interrupted:
            allowed_statuses.extend([RunStatus.RUNNING.value, RunStatus.PENDING.value])
        if allow_terminal_incomplete:
            allowed_statuses.extend([RunStatus.COMPLETED.value, RunStatus.COMPLETED_WITH_ERRORS.value])
        if run and run.status in allowed_statuses:
            run.status = RunStatus.RUNNING.value
            run.pause_requested = 0
            run.resume_count = (run.resume_count or 0) + 1
            run.completed_at = None
            run.error_message = None
            if run.started_at is None:
                run.started_at = datetime.utcnow()
            await self.session.commit()
            await self.session.refresh(run)
            return run
        return None

    async def set_pause_requested(self, id: str, value: int) -> Optional[Run]:
        """Set or clear the pause_requested flag (1 = pause, 0 = clear)."""
        run = await self.get_by_id(id)
        if run:
            run.pause_requested = value
            await self.session.commit()
        return run

    async def get_active_count(self) -> int:
        """Return the number of RUNNING or PENDING runs (scoped to user if user_uuid is set)."""
        stmt = (
            select(func.count())
            .select_from(Run)
            .where(Run.status.in_([RunStatus.RUNNING.value, RunStatus.PENDING.value]))
        )
        if self.user_uuid is not None:
            stmt = stmt.where(Run.user_uuid == self.user_uuid)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def update_progress(self, id: str, completed_tasks: int, failed_tasks: int = 0) -> Optional[Run]:
        """Update the progress of a run by task counts."""
        run = await self.get_by_id(id)
        if run:
            run.completed_tasks = completed_tasks
            run.failed_tasks = failed_tasks
            await self.session.commit()
            await self.session.refresh(run)
            return run
        return None

    async def set_total_tasks(self, id: str, total: int) -> Optional[Run]:
        """Set the total number of tasks for a run."""
        run = await self.get_by_id(id)
        if run:
            run.total_tasks = total
            await self.session.commit()
            await self.session.refresh(run)
            return run
        return None


    async def increment_completed_tasks(self, id: str, failed: bool = False) -> None:
        """Atomically increment completed_tasks (or failed_tasks) on a run.

        Uses a SQL UPDATE so multiple concurrent callbacks don't clobber each
        other's counts.  Does not raise if the run is not found.
        """
        from sqlalchemy import update as sa_update
        from app.infra.db.models.run import Run
        if failed:
            stmt = (
                sa_update(Run)
                .where(Run.id == id)
                .values(failed_tasks=Run.failed_tasks + 1)
                .execution_options(synchronize_session="fetch")
            )
        else:
            stmt = (
                sa_update(Run)
                .where(Run.id == id)
                .values(completed_tasks=Run.completed_tasks + 1)
                .execution_options(synchronize_session="fetch")
            )
        await self.session.execute(stmt)
        await self.session.commit()

    async def append_source_doc_timeline_event(
        self, id: str, source_doc_id: str, event: dict
    ) -> Optional[Run]:
        """
        Intentional no-op — kept for call-site compatibility.

        Timeline events are now stored in the normalized `run_timeline_events`
        table via RunResultsRepository.insert_timeline_event(), written in the
        PHASE-E completion block in presets.py / execution.py.

        The old `runs.results_summary` blob column that this method used to
        append to has been dropped.  Any call to this method is a dead call;
        the caller should use RunResultsRepository directly if it needs to
        persist a timeline event outside of PHASE-E.
        """
        return None
