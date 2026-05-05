"""
run_callbacks — shared DB write helpers for run execution callbacks.

Both execution.py and presets.py define on_gen_complete / on_eval_complete
callbacks.  Previously each file called RunResultsRepository directly,
which created two divergent write paths.  This module centralises ALL
callback DB writes through ResultsWriter (the validated write facade).

Usage:
    from app.services.run_callbacks import write_gen_doc, write_eval_scores

    # inside on_gen_complete:
    await write_gen_doc(run_id, config.user_uuid, doc_id, model,
                        generator, source_doc_id, iteration,
                        file_path="/abs/path/to/gen_doc.md")

    # inside on_eval_complete:
    await write_eval_scores(run_id, config.user_uuid, doc_id,
                            source_doc_id, result,
                            all_criteria, all_evaluators)

Rules:
- Every function is async and raises on failure (callers must NOT swallow).
- All DB access goes through ResultsWriter — never through RunResultsRepository
  directly from callback code.
- This module owns no state; it is purely a write helper.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from app.infra.db.session import get_user_session_by_uuid
from app.infra.db.repositories.task import TaskRepository
from app.services.results_writer import ResultsWriter

if TYPE_CHECKING:
    from app.evaluation.pairwise import PairwiseSummary
    from app.evaluation.models import SingleEvalResult
    from app.services.run_executor import GeneratedDocument, SourceDocResult

logger = logging.getLogger(__name__)


async def write_gen_doc(
    run_id: str,
    user_uuid: str,
    doc_id: str,
    model: str,
    generator: str,
    source_doc_id: str,
    iteration: int,
    file_path: Optional[str] = None,
    duration_seconds: Optional[float] = None,
    started_at: Optional[datetime] = None,
) -> None:
    """
    Persist one generated document to run_generated_docs via ResultsWriter.

    file_path should be the absolute path of the on-disk Markdown file so the
    run_generated_docs.file_path column is populated.  Pass None only when the
    path is genuinely unknown (legacy callers).

    Raises on any DB failure — the caller should NOT catch this silently.
    The run must fail rather than complete with missing attribution data.
    """
    async with get_user_session_by_uuid(user_uuid) as session:
        writer = ResultsWriter(session, run_id)
        inserted = await writer.write_generated_doc(
            doc_id=doc_id,
            source_doc_id=source_doc_id,
            generator=generator,
            model=model,
            iteration=iteration,
            completed_at=datetime.utcnow(),
            file_path=file_path,
            duration_seconds=duration_seconds,
            started_at=started_at,
        )
    if not inserted:
        # Already exists (ON CONFLICT DO NOTHING) — fine on resume, log for visibility.
        logger.debug(
            "[write_gen_doc] doc already existed (skipped): run=%s doc=%s generator=%s model=%s iter=%d",
            run_id[:8], doc_id[:12], generator, model, iteration,
        )
    else:
        logger.debug(
            "[write_gen_doc] inserted: run=%s doc=%s generator=%s model=%s iter=%d file_path=%s",
            run_id[:8], doc_id[:12], generator, model, iteration, file_path,
        )


async def write_eval_scores(
    run_id: str,
    user_uuid: str,
    doc_id: str,
    source_doc_id: str,
    trial: int,
    result: "SingleEvalResult",
    all_criteria: set[str],
    all_evaluators: set[str],
) -> None:
    """
    Persist all criterion scores for one eval result to run_eval_scores via
    ResultsWriter, then update the criteria/evaluator metadata lists.

    Raises on any DB failure — the caller should NOT catch this silently.

    The criteria_list and evaluator_list metadata are only rewritten when the
    sets actually grow (new name seen for the first time).  This avoids a
    full delete+insert on every callback invocation when nothing changed.

    Parameters
    ----------
    all_criteria / all_evaluators
        The *mutable* sets that the caller is maintaining for streaming UI.
        This function adds to them so the caller's in-memory state stays
        consistent without needing a second pass.
    """
    scored_at = datetime.utcnow()

    # Snapshot set sizes BEFORE adding new values so we know whether either
    # set grew and therefore whether the metadata lists need rewriting.
    criteria_size_before = len(all_criteria)
    evaluators_size_before = len(all_evaluators)

    async with get_user_session_by_uuid(user_uuid) as session:
        writer = ResultsWriter(session, run_id)

        for sc in result.scores:
            if not isinstance(sc.score, (int, float)) or isinstance(sc.score, bool):
                logger.warning(
                    "[write_eval_scores] skipped non-numeric score: "
                    "run=%s doc=%s criterion=%s value=%r",
                    run_id[:8], doc_id[:12], sc.criterion, sc.score,
                )
                continue

            await writer.write_eval_score(
                doc_id=doc_id,
                source_doc_id=source_doc_id,
                criterion=sc.criterion,
                judge_model=result.model,
                trial=trial,
                score=int(sc.score),
                reason=sc.reason,
                scored_at=scored_at,
            )
            all_criteria.add(sc.criterion)

        all_evaluators.add(result.model)

        # Only rewrite the metadata list rows when the sets have grown.
        # set_metadata_list does a full DELETE+INSERT for each key; calling it on
        # every eval callback when criteria/evaluators haven't changed is pure
        # write churn with no value.
        criteria_grew = len(all_criteria) > criteria_size_before
        evaluators_grew = len(all_evaluators) > evaluators_size_before
        if criteria_grew or evaluators_grew:
            from app.infra.db.repositories.run_results import RunResultsRepository
            repo = RunResultsRepository(session)
            if criteria_grew:
                await repo.set_metadata_list(run_id, "criteria_list", sorted(all_criteria))
            if evaluators_grew:
                await repo.set_metadata_list(run_id, "evaluator_list", sorted(all_evaluators))


async def write_pairwise_results(
    run_id: str,
    user_uuid: str,
    *,
    source_doc_id: str,
    summary: "PairwiseSummary",
    comparison_type: str,
) -> None:
    """Persist the normalized pairwise result rows for one completed phase."""
    async with get_user_session_by_uuid(user_uuid) as session:
        writer = ResultsWriter(session, run_id)
        for pair_result in summary.results:
            await writer.write_pairwise_result(
                source_doc_id=source_doc_id,
                doc_id_a=pair_result.doc_id_1,
                doc_id_b=pair_result.doc_id_2,
                winner_doc_id=pair_result.winner_doc_id,
                judge_model=pair_result.model,
                trial=pair_result.trial,
                reason=pair_result.reason,
                comparison_type=comparison_type,
                compared_at=pair_result.completed_at or pair_result.timestamp,
            )


async def write_combined_doc(
    run_id: str,
    user_uuid: str,
    *,
    generated_doc: "GeneratedDocument",
    input_doc_ids: list[str],
    combine_strategy: str,
    file_path: Optional[str] = None,
) -> None:
    """Persist one successful combined output row."""
    async with get_user_session_by_uuid(user_uuid) as session:
        writer = ResultsWriter(session, run_id)
        await writer.write_combined_doc(
            doc_id=generated_doc.doc_id,
            source_doc_id=generated_doc.source_doc_id,
            combine_model=generated_doc.model,
            combine_strategy=combine_strategy,
            input_doc_ids=input_doc_ids,
            duration_seconds=generated_doc.duration_seconds,
            started_at=generated_doc.started_at,
            completed_at=generated_doc.completed_at,
            file_path=file_path,
        )


async def write_source_doc_status(
    run_id: str,
    user_uuid: str,
    *,
    result: "SourceDocResult",
) -> None:
    """Persist one source-document terminal status row."""
    status = result.status.value if hasattr(result.status, "value") else str(result.status)
    async with get_user_session_by_uuid(user_uuid) as session:
        writer = ResultsWriter(session, run_id)
        await writer.write_source_doc_status(
            source_doc_id=result.source_doc_id,
            source_doc_name=result.source_doc_name,
            status=status,
            winner_doc_id=result.winner_doc_id,
            error_message="; ".join(result.errors) if result.errors else None,
            started_at=result.started_at,
            completed_at=result.completed_at,
        )


async def mark_phase_checkpoint_completed(
    run_id: str,
    user_uuid: str,
    *,
    source_doc_id: str,
    phase: str,
    model_name: str,
    iteration: int,
    output_ref: str,
    generator: Optional[str] = None,
    judge_model: Optional[str] = None,
) -> None:
    """Create and complete a generic phase checkpoint task if it does not already exist."""
    async with get_user_session_by_uuid(user_uuid) as session:
        task_repo = TaskRepository(session)
        existing = await task_repo.find_completed_phase_task(
            run_id,
            source_doc_id,
            phase=phase,
            model_name=model_name,
            iteration=iteration,
        )
        if existing is not None:
            return
        task = await task_repo.create_phase_task(
            run_id=run_id,
            source_doc_id=source_doc_id,
            model_name=model_name,
            iteration=iteration,
            phase=phase,
            generator=generator,
            judge_model=judge_model,
        )
        await task_repo.complete_with_output(
            task.id,
            output_ref=output_ref,
        )
