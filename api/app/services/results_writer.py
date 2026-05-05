"""
ResultsWriter — the single validated write entry point for all run-result data.

RULES:
- Every write method validates its inputs before touching the DB.
- Unexpected / malformed data is DISCARDED and logged with structured context.
- No exceptions propagate to callers — all failures are caught and logged here.
- All methods are async and return bool (True = written, False = discarded/failed).

Validation constants own the definition of "valid".  If a new generator,
phase, or score range is added to the application, update the constants here.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.repositories.run_results import RunResultsRepository

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Validation constants — single source of truth for allowed values
# ---------------------------------------------------------------------------

VALID_GENERATORS: frozenset[str] = frozenset({"fpf", "gptr", "dr", "combine", "aiq"})

VALID_PHASES: frozenset[str] = frozenset({
    "generation",
    "single_eval",
    "pairwise",
    "combine",
    "post_combine_eval",
    "run_setup",
    "run_complete",
    "run_failed",
})

VALID_COMPARISON_TYPES: frozenset[str] = frozenset({"pre_combine", "post_combine"})

SCORE_MIN: int = 1
SCORE_MAX: int = 5


# ---------------------------------------------------------------------------
# Log helpers — structured, no sensitive payload dumps
# ---------------------------------------------------------------------------

def _discard(reason: str, **ctx: object) -> None:
    """Log a discarded write with structured context."""
    parts = " ".join(f"{k}={v!r}" for k, v in ctx.items())
    logger.error("[DISCARD] %s: %s", reason, parts)


def _coerce(reason: str, **ctx: object) -> None:
    """Log a coerced (auto-corrected) write with structured context."""
    parts = " ".join(f"{k}={v!r}" for k, v in ctx.items())
    logger.warning("[COERCE] %s: %s", reason, parts)


# ---------------------------------------------------------------------------
# ResultsWriter
# ---------------------------------------------------------------------------

class ResultsWriter:
    """
    Validated write facade over RunResultsRepository.

    Instantiate once per run execution context, bound to the user's AsyncSession.

    Example:
        writer = ResultsWriter(session, run_id="abc-123")
        ok = await writer.write_eval_score(
            doc_id="doc-1", source_doc_id="src-1",
            criterion="accuracy", judge_model="gpt-4o",
            trial=1, score=4,
        )
    """

    def __init__(self, session: AsyncSession, run_id: str) -> None:
        self._repo = RunResultsRepository(session)
        self._run_id = run_id

    # -----------------------------------------------------------------------
    # Eval scores
    # -----------------------------------------------------------------------

    async def write_eval_score(
        self,
        *,
        doc_id: str,
        source_doc_id: str,
        criterion: str,
        judge_model: str,
        trial: int,
        score: object,
        reason: Optional[str] = None,
        scored_at: Optional[datetime] = None,
    ) -> bool:
        """
        Validate and persist one eval score.

        Validation order:
        1. doc_id, source_doc_id, criterion, judge_model must be non-empty strings.
        2. trial must be a positive integer.
        3. score must be numeric; non-integer floats are coerced to int with warning.
        4. Coerced or original integer score must be in [SCORE_MIN, SCORE_MAX].
        5. Anything else → discard + error log → return False.
        """
        run_id = self._run_id
        ctx = dict(run_id=run_id, doc_id=doc_id, criterion=criterion,
                   judge_model=judge_model, trial=trial)

        # ── string field validation ─────────────────────────────────────────
        for field_name, field_val in (
            ("doc_id", doc_id),
            ("source_doc_id", source_doc_id),
            ("criterion", criterion),
            ("judge_model", judge_model),
        ):
            if not isinstance(field_val, str) or not field_val.strip():
                _discard(f"empty or non-string {field_name}", value=field_val, **ctx)
                return False

        if not isinstance(trial, int) or trial < 1:
            _discard("invalid trial number", value=trial, **ctx)
            return False

        # ── score validation ────────────────────────────────────────────────
        if score is None:
            _discard("score is None", value=score, **ctx)
            return False

        if isinstance(score, bool):
            # bool is subclass of int in Python — reject it
            _discard("score is bool not numeric", value=score, **ctx)
            return False

        if isinstance(score, float):
            if not score.is_integer():
                _coerce("float score coerced to int", value=score, **ctx)
            score = int(score)
        elif not isinstance(score, int):
            _discard("score is not numeric", value=score, **ctx)
            return False

        if not (SCORE_MIN <= score <= SCORE_MAX):
            _discard(
                f"score out of range [{SCORE_MIN}–{SCORE_MAX}]",
                value=score, **ctx,
            )
            return False

        # ── persist ─────────────────────────────────────────────────────────
        try:
            inserted = await self._repo.insert_eval_score(
                run_id=run_id,
                doc_id=doc_id,
                source_doc_id=source_doc_id,
                criterion=criterion,
                judge_model=judge_model,
                trial=trial,
                score=score,
                reason=reason,
                scored_at=scored_at,
            )
            return inserted
        except Exception as exc:
            logger.error(
                "[ResultsWriter] insert_eval_score failed: %s | run=%s doc=%s criterion=%s",
                exc, run_id, doc_id, criterion, exc_info=True,
            )
            return False

    # -----------------------------------------------------------------------
    # Generated documents
    # -----------------------------------------------------------------------

    async def write_generated_doc(
        self,
        *,
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
        run_id = self._run_id
        ctx = dict(run_id=run_id, doc_id=doc_id, generator=generator)

        if not isinstance(doc_id, str) or not doc_id.strip():
            _discard("empty doc_id", **ctx)
            return False
        if not isinstance(source_doc_id, str) or not source_doc_id.strip():
            _discard("empty source_doc_id", **ctx)
            return False
        if generator not in VALID_GENERATORS:
            _discard(f"unknown generator (valid: {sorted(VALID_GENERATORS)})", **ctx)
            return False
        if not isinstance(model, str) or not model.strip():
            _discard("empty model", **ctx)
            return False

        try:
            return await self._repo.insert_generated_doc(
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
        except Exception as exc:
            logger.error(
                "[ResultsWriter] insert_generated_doc failed: %s | run=%s doc=%s",
                exc, run_id, doc_id, exc_info=True,
            )
            return False

    # -----------------------------------------------------------------------
    # Pairwise results
    # -----------------------------------------------------------------------

    async def write_pairwise_result(
        self,
        *,
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
        run_id = self._run_id
        ctx = dict(run_id=run_id, source_doc_id=source_doc_id,
                   doc_id_a=doc_id_a, doc_id_b=doc_id_b, judge_model=judge_model)

        if comparison_type not in VALID_COMPARISON_TYPES:
            _discard(
                f"unknown comparison_type (valid: {sorted(VALID_COMPARISON_TYPES)})",
                comparison_type=comparison_type, **ctx,
            )
            return False

        for field_name, val in (
            ("source_doc_id", source_doc_id),
            ("doc_id_a", doc_id_a),
            ("doc_id_b", doc_id_b),
            ("judge_model", judge_model),
        ):
            if not isinstance(val, str) or not val.strip():
                _discard(f"empty {field_name}", **ctx)
                return False

        if doc_id_a == doc_id_b:
            _discard("doc_id_a and doc_id_b are identical", **ctx)
            return False

        try:
            return await self._repo.insert_pairwise_result(
                run_id=run_id,
                source_doc_id=source_doc_id,
                doc_id_a=doc_id_a,
                doc_id_b=doc_id_b,
                winner_doc_id=winner_doc_id,
                judge_model=judge_model,
                trial=trial,
                reason=reason,
                comparison_type=comparison_type,
                compared_at=compared_at,
            )
        except Exception as exc:
            logger.error(
                "[ResultsWriter] insert_pairwise_result failed: %s | run=%s",
                exc, run_id, exc_info=True,
            )
            return False

    # -----------------------------------------------------------------------
    # Timeline events
    # -----------------------------------------------------------------------

    async def write_timeline_event(
        self,
        *,
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
    ) -> bool:
        run_id = self._run_id

        if phase not in VALID_PHASES:
            _discard(
                f"unknown phase (valid: {sorted(VALID_PHASES)})",
                run_id=run_id, phase=phase, event_type=event_type,
            )
            return False

        if not isinstance(event_type, str) or not event_type.strip():
            _discard("empty event_type", run_id=run_id, phase=phase)
            return False

        try:
            await self._repo.insert_timeline_event(
                run_id=run_id,
                phase=phase,
                event_type=event_type,
                source_doc_id=source_doc_id,
                doc_id=doc_id,
                description=description,
                model=model,
                success=success,
                duration_seconds=duration_seconds,
                details_json=details_json,
                occurred_at=occurred_at,
            )
            return True
        except Exception as exc:
            logger.error(
                "[ResultsWriter] insert_timeline_event failed: %s | run=%s phase=%s",
                exc, run_id, phase, exc_info=True,
            )
            return False

    # -----------------------------------------------------------------------
    # Source document status
    # -----------------------------------------------------------------------

    async def write_source_doc_status(
        self,
        *,
        source_doc_id: str,
        status: str,
        source_doc_name: Optional[str] = None,
        winner_doc_id: Optional[str] = None,
        error_message: Optional[str] = None,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
    ) -> bool:
        run_id = self._run_id

        if not isinstance(source_doc_id, str) or not source_doc_id.strip():
            _discard("empty source_doc_id", run_id=run_id, status=status)
            return False

        if not isinstance(status, str) or not status.strip():
            _discard("empty status", run_id=run_id, source_doc_id=source_doc_id)
            return False

        try:
            await self._repo.upsert_source_doc_status(
                run_id=run_id,
                source_doc_id=source_doc_id,
                source_doc_name=source_doc_name,
                status=status,
                winner_doc_id=winner_doc_id,
                error_message=error_message,
                started_at=started_at,
                completed_at=completed_at,
            )
            return True
        except Exception as exc:
            logger.error(
                "[ResultsWriter] upsert_source_doc_status failed: %s | run=%s src=%s",
                exc, run_id, source_doc_id, exc_info=True,
            )
            return False

    # -----------------------------------------------------------------------
    # Combined documents
    # -----------------------------------------------------------------------

    async def write_combined_doc(
        self,
        *,
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
        run_id = self._run_id
        ctx = dict(run_id=run_id, doc_id=doc_id, combine_model=combine_model)

        for field_name, val in (
            ("doc_id", doc_id),
            ("source_doc_id", source_doc_id),
            ("combine_model", combine_model),
            ("combine_strategy", combine_strategy),
        ):
            if not isinstance(val, str) or not val.strip():
                _discard(f"empty {field_name}", **ctx)
                return False

        if not isinstance(input_doc_ids, list) or not input_doc_ids:
            _discard("input_doc_ids must be a non-empty list", **ctx)
            return False

        try:
            return await self._repo.insert_combined_doc(
                run_id=run_id,
                doc_id=doc_id,
                source_doc_id=source_doc_id,
                combine_model=combine_model,
                combine_strategy=combine_strategy,
                input_doc_ids=input_doc_ids,
                duration_seconds=duration_seconds,
                started_at=started_at,
                completed_at=completed_at,
                file_path=file_path,
            )
        except Exception as exc:
            logger.error(
                "[ResultsWriter] insert_combined_doc failed: %s | run=%s doc=%s",
                exc, run_id, doc_id, exc_info=True,
            )
            return False
