"""
RunResultsRepository — atomic read/write for all normalized run-result tables.

Every method operates on a single row type in a single table.
No blob reads or writes. No read-modify-write cycles.

Write methods use INSERT OR IGNORE (or INSERT OR REPLACE for upsert targets)
so they are safe under concurrent callbacks and run resume/replay.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional, Sequence

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models.run_results import (
    RunCombinedDoc,
    RunEvalScore,
    RunGeneratedDoc,
    RunMetadata,
    RunMetadataList,
    RunPairwiseResult,
    RunSourceDocStatus,
    RunTimelineEvent,
)

logger = logging.getLogger(__name__)


class RunResultsRepository:
    """
    Repository for all normalized run-result tables.

    Injected with an AsyncSession scoped to a single user DB.
    Does NOT handle `runs` table lifecycle — that stays in RunRepository.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -----------------------------------------------------------------------
    # Generated documents
    # -----------------------------------------------------------------------

    async def insert_generated_doc(
        self,
        *,
        run_id: str,
        doc_id: str,
        source_doc_id: str,
        generator: str,
        model: str,
        iteration: int = 1,
        duration_seconds: Optional[float] = None,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        file_path: Optional[str] = None,
    ) -> bool:
        """
        Insert a generated document row.  Silently ignores duplicates.
        Returns True if inserted, False if already existed.
        """
        stmt = (
            sqlite_insert(RunGeneratedDoc)
            .values(
                run_id=run_id,
                doc_id=doc_id,
                source_doc_id=source_doc_id,
                generator=generator,
                model=model,
                iteration=iteration,
                duration_seconds=duration_seconds,
                started_at=started_at,
                completed_at=completed_at,
                file_path=file_path,
            )
            .on_conflict_do_nothing(index_elements=["run_id", "doc_id"])
        )
        result = await self.session.execute(stmt)
        return result.rowcount > 0

    async def get_generated_docs(
        self,
        run_id: str,
        source_doc_id: Optional[str] = None,
        generator: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> Sequence[RunGeneratedDoc]:
        stmt = select(RunGeneratedDoc).where(RunGeneratedDoc.run_id == run_id)
        if source_doc_id:
            stmt = stmt.where(RunGeneratedDoc.source_doc_id == source_doc_id)
        if generator:
            stmt = stmt.where(RunGeneratedDoc.generator == generator)
        stmt = stmt.order_by(RunGeneratedDoc.completed_at)
        if offset:
            stmt = stmt.offset(offset)
        if limit:
            stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count_generated_docs(
        self,
        run_id: str,
        source_doc_id: Optional[str] = None,
    ) -> int:
        stmt = select(func.count(RunGeneratedDoc.id)).where(RunGeneratedDoc.run_id == run_id)
        if source_doc_id:
            stmt = stmt.where(RunGeneratedDoc.source_doc_id == source_doc_id)
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)

    # -----------------------------------------------------------------------
    # Evaluation scores
    # -----------------------------------------------------------------------

    async def insert_eval_score(
        self,
        *,
        run_id: str,
        doc_id: str,
        source_doc_id: str,
        criterion: str,
        judge_model: str,
        trial: int,
        score: int,
        reason: Optional[str] = None,
        scored_at: Optional[datetime] = None,
    ) -> bool:
        """
        Insert one eval score row.  Silently ignores duplicates.
        Returns True if inserted, False if already existed.

        Score validation (range 1–5, integer-only) is enforced in ResultsWriter
        before this method is called.  This method trusts its inputs.
        """
        stmt = (
            sqlite_insert(RunEvalScore)
            .values(
                run_id=run_id,
                doc_id=doc_id,
                source_doc_id=source_doc_id,
                criterion=criterion,
                judge_model=judge_model,
                trial=trial,
                score=score,
                reason=reason,
                scored_at=scored_at or datetime.utcnow(),
            )
            .on_conflict_do_nothing(
                index_elements=["run_id", "doc_id", "criterion", "judge_model", "trial"]
            )
        )
        result = await self.session.execute(stmt)
        return result.rowcount > 0

    async def get_eval_scores(
        self,
        run_id: str,
        doc_id: Optional[str] = None,
        source_doc_id: Optional[str] = None,
        criterion: Optional[str] = None,
        judge_model: Optional[str] = None,
    ) -> Sequence[RunEvalScore]:
        stmt = select(RunEvalScore).where(RunEvalScore.run_id == run_id)
        if doc_id:
            stmt = stmt.where(RunEvalScore.doc_id == doc_id)
        if source_doc_id:
            stmt = stmt.where(RunEvalScore.source_doc_id == source_doc_id)
        if criterion:
            stmt = stmt.where(RunEvalScore.criterion == criterion)
        if judge_model:
            stmt = stmt.where(RunEvalScore.judge_model == judge_model)
        stmt = stmt.order_by(
            RunEvalScore.doc_id,
            RunEvalScore.criterion,
            RunEvalScore.judge_model,
            RunEvalScore.trial,
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_eval_aggregates(
        self,
        run_id: str,
        doc_id: Optional[str] = None,
        source_doc_id: Optional[str] = None,
        doc_ids: Optional[Sequence[str]] = None,
    ) -> Sequence[dict[str, Any]]:
        """
        Return per-(doc_id, criterion, judge_model) average score.

        Result rows: {doc_id, source_doc_id, criterion, judge_model, avg_score, trial_count, reason}
        reason: MIN(reason) across trials — preserves the justification text for tooltip display.
        """
        stmt = (
            select(
                RunEvalScore.doc_id,
                RunEvalScore.source_doc_id,
                RunEvalScore.criterion,
                RunEvalScore.judge_model,
                func.avg(RunEvalScore.score).label("avg_score"),
                func.count(RunEvalScore.id).label("trial_count"),
                func.min(RunEvalScore.reason).label("reason"),
            )
            .where(RunEvalScore.run_id == run_id)
        )
        if doc_id:
            stmt = stmt.where(RunEvalScore.doc_id == doc_id)
        if source_doc_id:
            stmt = stmt.where(RunEvalScore.source_doc_id == source_doc_id)
        if doc_ids:
            stmt = stmt.where(RunEvalScore.doc_id.in_(list(doc_ids)))
        stmt = stmt.group_by(
            RunEvalScore.doc_id,
            RunEvalScore.source_doc_id,
            RunEvalScore.criterion,
            RunEvalScore.judge_model,
        )
        result = await self.session.execute(stmt)
        return [row._asdict() for row in result.all()]

    # -----------------------------------------------------------------------
    # Pairwise results
    # -----------------------------------------------------------------------

    async def insert_pairwise_result(
        self,
        *,
        run_id: str,
        source_doc_id: str,
        doc_id_a: str,
        doc_id_b: str,
        winner_doc_id: Optional[str],
        judge_model: str,
        trial: int = 1,
        reason: Optional[str] = None,
        comparison_type: str = "pre_combine",
        compared_at: Optional[datetime] = None,
    ) -> bool:
        stmt = (
            sqlite_insert(RunPairwiseResult)
            .values(
                run_id=run_id,
                source_doc_id=source_doc_id,
                doc_id_a=doc_id_a,
                doc_id_b=doc_id_b,
                winner_doc_id=winner_doc_id,
                judge_model=judge_model,
                trial=trial,
                reason=reason,
                comparison_type=comparison_type,
                compared_at=compared_at or datetime.utcnow(),
            )
            .on_conflict_do_nothing(
                index_elements=[
                    "run_id", "source_doc_id", "doc_id_a", "doc_id_b",
                    "judge_model", "trial", "comparison_type",
                ]
            )
        )
        result = await self.session.execute(stmt)
        return result.rowcount > 0

    async def get_pairwise_results(
        self,
        run_id: str,
        source_doc_id: Optional[str] = None,
        comparison_type: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> Sequence[RunPairwiseResult]:
        stmt = select(RunPairwiseResult).where(RunPairwiseResult.run_id == run_id)
        if source_doc_id:
            stmt = stmt.where(RunPairwiseResult.source_doc_id == source_doc_id)
        if comparison_type:
            stmt = stmt.where(RunPairwiseResult.comparison_type == comparison_type)
        stmt = stmt.order_by(RunPairwiseResult.compared_at)
        if limit is not None:
            stmt = stmt.limit(limit)
        if offset:
            stmt = stmt.offset(offset)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def delete_pairwise_results(
        self,
        run_id: str,
        *,
        source_doc_id: str,
        comparison_type: str,
    ) -> int:
        """Delete normalized pairwise rows for one source-doc comparison family."""
        stmt = (
            delete(RunPairwiseResult)
            .where(RunPairwiseResult.run_id == run_id)
            .where(RunPairwiseResult.source_doc_id == source_doc_id)
            .where(RunPairwiseResult.comparison_type == comparison_type)
        )
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)

    # -----------------------------------------------------------------------
    # Timeline events
    # -----------------------------------------------------------------------

    async def insert_timeline_event(
        self,
        *,
        run_id: str,
        phase: str,
        event_type: str,
        source_doc_id: Optional[str] = None,
        doc_id: Optional[str] = None,
        description: Optional[str] = None,
        model: Optional[str] = None,
        success: bool = True,
        duration_seconds: Optional[float] = None,
        details_json: Optional[str] = None,
        occurred_at: Optional[datetime] = None,
    ) -> int:
        """Insert a timeline event.  No dedup — every event is its own row.  Returns new row id."""
        row = RunTimelineEvent(
            run_id=run_id,
            source_doc_id=source_doc_id,
            doc_id=doc_id,
            phase=phase,
            event_type=event_type,
            description=description,
            model=model,
            success=success,
            duration_seconds=duration_seconds,
            details_json=details_json,
            occurred_at=occurred_at or datetime.utcnow(),
        )
        self.session.add(row)
        await self.session.flush()
        return row.id  # type: ignore[return-value]

    async def get_timeline_events(
        self,
        run_id: str,
        source_doc_id: Optional[str] = None,
        phase: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> Sequence[RunTimelineEvent]:
        stmt = select(RunTimelineEvent).where(RunTimelineEvent.run_id == run_id)
        if source_doc_id:
            stmt = stmt.where(RunTimelineEvent.source_doc_id == source_doc_id)
        if phase:
            stmt = stmt.where(RunTimelineEvent.phase == phase)
        stmt = stmt.order_by(RunTimelineEvent.occurred_at)
        if limit is not None:
            stmt = stmt.limit(limit)
        if offset:
            stmt = stmt.offset(offset)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count_timeline_events(
        self,
        run_id: str,
        source_doc_id: Optional[str] = None,
        phase: Optional[str] = None,
    ) -> int:
        stmt = select(func.count(RunTimelineEvent.id)).where(RunTimelineEvent.run_id == run_id)
        if source_doc_id:
            stmt = stmt.where(RunTimelineEvent.source_doc_id == source_doc_id)
        if phase:
            stmt = stmt.where(RunTimelineEvent.phase == phase)
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)

    # -----------------------------------------------------------------------
    # Combined documents
    # -----------------------------------------------------------------------

    async def insert_combined_doc(
        self,
        *,
        run_id: str,
        doc_id: str,
        source_doc_id: str,
        combine_model: str,
        combine_strategy: str,
        input_doc_ids: list[str],
        duration_seconds: Optional[float] = None,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        file_path: Optional[str] = None,
    ) -> bool:
        stmt = (
            sqlite_insert(RunCombinedDoc)
            .values(
                run_id=run_id,
                doc_id=doc_id,
                source_doc_id=source_doc_id,
                combine_model=combine_model,
                combine_strategy=combine_strategy,
                input_doc_ids=",".join(input_doc_ids),
                duration_seconds=duration_seconds,
                started_at=started_at,
                completed_at=completed_at,
                file_path=file_path,
            )
            .on_conflict_do_nothing(index_elements=["run_id", "doc_id"])
        )
        result = await self.session.execute(stmt)
        return result.rowcount > 0

    async def get_combined_docs(
        self, run_id: str, source_doc_id: Optional[str] = None
    ) -> Sequence[RunCombinedDoc]:
        stmt = select(RunCombinedDoc).where(RunCombinedDoc.run_id == run_id)
        if source_doc_id:
            stmt = stmt.where(RunCombinedDoc.source_doc_id == source_doc_id)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def delete_combined_docs(
        self,
        run_id: str,
        *,
        source_doc_id: str,
        combine_model: Optional[str] = None,
    ) -> int:
        """Delete normalized combined-doc rows for one source doc, optionally one model."""
        stmt = (
            delete(RunCombinedDoc)
            .where(RunCombinedDoc.run_id == run_id)
            .where(RunCombinedDoc.source_doc_id == source_doc_id)
        )
        if combine_model is not None:
            stmt = stmt.where(RunCombinedDoc.combine_model == combine_model)
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)

    # -----------------------------------------------------------------------
    # Source document status
    # -----------------------------------------------------------------------

    async def upsert_source_doc_status(
        self,
        *,
        run_id: str,
        source_doc_id: str,
        source_doc_name: Optional[str] = None,
        status: str,
        winner_doc_id: Optional[str] = None,
        error_message: Optional[str] = None,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
    ) -> None:
        """Insert or replace source-doc status row (full replace on conflict)."""
        stmt = (
            sqlite_insert(RunSourceDocStatus)
            .values(
                run_id=run_id,
                source_doc_id=source_doc_id,
                source_doc_name=source_doc_name,
                status=status,
                winner_doc_id=winner_doc_id,
                error_message=error_message,
                started_at=started_at,
                completed_at=completed_at,
            )
            .on_conflict_do_update(
                index_elements=["run_id", "source_doc_id"],
                set_={
                    # Preserve existing name if new value is NULL (PHASE-E writes no name)
                    "source_doc_name": func.coalesce(
                        text("excluded.source_doc_name"),
                        RunSourceDocStatus.source_doc_name,
                    ),
                    "status": status,
                    "winner_doc_id": winner_doc_id,
                    "error_message": error_message,
                    "started_at": started_at,
                    "completed_at": completed_at,
                },
            )
        )
        await self.session.execute(stmt)

    async def get_source_doc_statuses(
        self, run_id: str, source_doc_id: Optional[str] = None
    ) -> Sequence[RunSourceDocStatus]:
        stmt = (
            select(RunSourceDocStatus)
            .where(RunSourceDocStatus.run_id == run_id)
            .order_by(RunSourceDocStatus.source_doc_id)
        )
        if source_doc_id:
            stmt = stmt.where(RunSourceDocStatus.source_doc_id == source_doc_id)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    # -----------------------------------------------------------------------
    # Run metadata — scalar
    # -----------------------------------------------------------------------

    async def set_metadata(self, run_id: str, key: str, value: str) -> None:
        """Set (insert or replace) a scalar metadata key for a run."""
        stmt = (
            sqlite_insert(RunMetadata)
            .values(run_id=run_id, key=key, value=value)
            .on_conflict_do_update(
                index_elements=["run_id", "key"],
                set_={"value": value},
            )
        )
        await self.session.execute(stmt)

    async def get_metadata(self, run_id: str) -> dict[str, str]:
        """Return all scalar metadata for a run as a plain dict."""
        stmt = select(RunMetadata).where(RunMetadata.run_id == run_id)
        result = await self.session.execute(stmt)
        return {row.key: row.value for row in result.scalars().all()}

    async def delete_metadata_keys(self, run_id: str, keys: Sequence[str]) -> None:
        """Delete a set of scalar metadata keys for a run."""
        if not keys:
            return
        stmt = delete(RunMetadata).where(
            RunMetadata.run_id == run_id,
            RunMetadata.key.in_(list(keys)),
        )
        await self.session.execute(stmt)

    async def get_metadata_value(
        self, run_id: str, key: str
    ) -> Optional[str]:
        stmt = (
            select(RunMetadata.value)
            .where(RunMetadata.run_id == run_id, RunMetadata.key == key)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # -----------------------------------------------------------------------
    # Run metadata — list
    # -----------------------------------------------------------------------

    async def set_metadata_list(
        self, run_id: str, key: str, items: list[str]
    ) -> None:
        """
        Replace the ordered list for (run_id, key) with the provided items.

        Deletes existing rows for this (run_id, key) then inserts fresh.
        """
        await self.session.execute(
            text(
                "DELETE FROM run_metadata_list WHERE run_id = :run_id AND key = :key"
            ),
            {"run_id": run_id, "key": key},
        )
        if items:
            await self.session.execute(
                sqlite_insert(RunMetadataList),
                [
                    {"run_id": run_id, "key": key, "position": pos, "value": val}
                    for pos, val in enumerate(items)
                ],
            )

    async def get_metadata_list(self, run_id: str, key: str) -> list[str]:
        """Return ordered list items for (run_id, key)."""
        stmt = (
            select(RunMetadataList.value)
            .where(
                RunMetadataList.run_id == run_id,
                RunMetadataList.key == key,
            )
            .order_by(RunMetadataList.position)
        )
        result = await self.session.execute(stmt)
        return [row for row in result.scalars().all()]

    async def get_all_metadata_lists(self, run_id: str) -> dict[str, list[str]]:
        """Return all list metadata for a run as key → [values]."""
        stmt = (
            select(RunMetadataList)
            .where(RunMetadataList.run_id == run_id)
            .order_by(RunMetadataList.key, RunMetadataList.position)
        )
        result = await self.session.execute(stmt)
        output: dict[str, list[str]] = {}
        for row in result.scalars().all():
            output.setdefault(row.key, []).append(row.value)
        return output

    # -----------------------------------------------------------------------
    # Bulk delete (for run deletion cascades where FK cascade isn't available)
    # -----------------------------------------------------------------------

    async def delete_all_for_run(self, run_id: str) -> None:
        """Remove all normalized result rows for a given run_id."""
        for table in (
            "run_generated_docs",
            "run_eval_scores",
            "run_pairwise_results",
            "run_timeline_events",
            "run_combined_docs",
            "run_source_doc_status",
            "run_metadata",
            "run_metadata_list",
        ):
            await self.session.execute(
                text(f"DELETE FROM {table} WHERE run_id = :run_id"),
                {"run_id": run_id},
            )
