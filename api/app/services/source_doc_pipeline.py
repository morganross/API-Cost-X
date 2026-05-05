"""
Source Document Pipeline.

Encapsulates the complete execution pipeline for a single source document.
Each source document runs through its own isolated pipeline:
  Generation → Single Eval → Pairwise → Combine → Post-Combine Eval

Documents NEVER compete across source doc boundaries - each produces its own winner.

This supports pipelined concurrency where multiple SourceDocPipelines can run
simultaneously, sharing a global API semaphore for rate limiting.
"""

import asyncio
import itertools
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4


from app.services.log_writer import _current_capture_id
from app.infra.db.repositories.run_results import RunResultsRepository
from app.infra.db.repositories.task import TaskRepository
from app.infra.db.session import get_user_session_by_uuid

from ..adapters.base import GenerationConfig, GeneratorType, ProgressCallback
from ..adapters.fpf.adapter import FpfAdapter
from ..adapters.fpf.errors import FpfExecutionError
from ..adapters.gptr.adapter import GptrAdapter
from ..adapters.dr.adapter import DrAdapter
from ..adapters.aiq.adapter import AiqAdapter
from ..adapters.combine.adapter import CombineAdapter
from ..evaluation import (
    DocumentInput,
    SingleDocEvaluator,
    SingleEvalConfig,
    SingleEvalSummary,
    PairwiseEvaluator,
    PairwiseConfig,
    PairwiseSummary,
    FpfStatsTracker,
    EloCalculator,
)
from ..evaluation.models import PairwiseResult
from ..evaluation.models import EvaluationCriterion
from ..evaluation.criteria import CriteriaManager, parse_criteria_yaml
from .rate_limiter import RateLimitedRequest
from .config_builder import (
    compile_combine_adapter_extra,
    compile_generation_adapter_extra,
)
from .run_callbacks import (
    mark_phase_checkpoint_completed,
    write_combined_doc,
    write_pairwise_results,
    write_source_doc_status,
)

from .run_executor import (
    RunConfig,
    RunPhase,
    GeneratedDocument,
    SourceDocResult,
)


class SourceDocPipeline:
    """
    Executes the full pipeline for a single source document.

    Each source document is completely isolated - its generated variations
    only compete against each other, never against other source documents.

    The pipeline phases are serial within a document:
    1. Generation + Single Eval (concurrent API calls within phase)
    2. Pairwise Evaluation (after all generation complete)
    3. Combine (merge top docs)
    4. Post-Combine Eval (optional)

    Multiple SourceDocPipelines can run concurrently, sharing a global API
    semaphore for rate limiting across all pipelines.
    """

    def __init__(
        self,
        source_doc_id: str,
        source_doc_name: str,
        content: str,
        config: RunConfig,
        run_id: str,
        shared_semaphore: asyncio.Semaphore,
        stats_tracker: Optional[FpfStatsTracker] = None,
        fpf_adapter: Optional[FpfAdapter] = None,
        gptr_adapter: Optional[GptrAdapter] = None,
        dr_adapter: Optional[DrAdapter] = None,
        aiq_adapter: Optional[AiqAdapter] = None,
        logger: Optional[logging.Logger] = None,
        run_store: Optional[Any] = None,
        on_timeline_event: Optional[Callable] = None,
        log_writer=None,
    ):
        """
        Initialize a source document pipeline.

        Args:
            source_doc_id: Unique ID for this source document
            source_doc_name: Human-readable name for UI display
            content: The actual document content to process
            config: Full run configuration (shared across all pipelines)
            run_id: The parent run ID
            shared_semaphore: API rate limiting semaphore (shared across all pipelines)
            stats_tracker: FPF stats tracker (shared across all pipelines)
            fpf_adapter: Shared FPF adapter instance
            gptr_adapter: Shared GPTR adapter instance
            dr_adapter: Shared DR adapter instance
            logger: Logger instance (uses module logger if not provided)
            run_store: Run store for persisting progress
            on_timeline_event: Callback for timeline events
        """
        self.source_doc_id = source_doc_id
        self.source_doc_name = source_doc_name
        self.content = content
        self.config = config
        self.run_id = run_id
        self.semaphore = shared_semaphore
        self.stats = stats_tracker
        # Fallback logger uses a private name with NullHandler — ensures document names
        # from Pipeline [{source_doc_name}] prefix NEVER propagate to admin root logger.
        if logger:
            self.logger = logger
        else:
            _fallback = logging.getLogger("null_pipeline")
            if not _fallback.handlers:
                _fallback.addHandler(logging.NullHandler())
                _fallback.propagate = False
            self.logger = _fallback
        self.run_store = run_store
        self.on_timeline_event = on_timeline_event
        self._log_writer = log_writer

        # Adapters (shared across pipelines for efficiency)
        self._fpf_adapter = fpf_adapter or FpfAdapter()
        self._gptr_adapter = gptr_adapter or GptrAdapter()
        self._dr_adapter = dr_adapter or DrAdapter()
        self._aiq_adapter = aiq_adapter or AiqAdapter()
        self._active_adapter_tasks: Dict[str, GeneratorType] = {}
        self._locked_invariant_failures: list[dict[str, Any]] = []

        # Cancellation / pause flags (can be set externally)
        self._cancelled = False
        self._paused = False

    def _get_run_root(self) -> Path:
        from ..config import get_settings

        settings = get_settings()
        return settings.data_dir / f"user_{self.config.user_uuid}" / "runs" / self.run_id

    def cancel(self) -> None:
        """Cancel this pipeline."""
        self._cancelled = True
        if not self._active_adapter_tasks:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        for task_id, generator in list(self._active_adapter_tasks.items()):
            try:
                loop.create_task(self._get_adapter(generator).cancel(task_id))
            except Exception:
                continue

    def pause(self) -> None:
        """Pause this pipeline (drain-and-hold: in-flight tasks finish, queued tasks wait)."""
        self._paused = True

    def resume_pipeline(self) -> None:
        """Clear the pause flag so queued tasks can proceed (called on resume)."""
        self._paused = False

    def _stop_requested(self) -> bool:
        """Return True when cooperative pause/cancel should halt new work."""
        return self._cancelled or self._paused

    async def _acquire_semaphore_or_stop(
        self,
        semaphore: asyncio.Semaphore,
        *,
        poll_interval: float = 0.1,
    ) -> bool:
        """Wait for a semaphore slot, but re-check pause/cancel while queued."""
        while not self._stop_requested():
            try:
                await asyncio.wait_for(semaphore.acquire(), timeout=poll_interval)
                return True
            except asyncio.TimeoutError:
                continue
        return False

    def _mark_result_stopped(
        self,
        result: SourceDocResult,
        *,
        started_at: datetime,
    ) -> SourceDocResult:
        """Finalize a pipeline result after a cooperative pause/cancel request."""
        result.status = RunPhase.CANCELLED
        result.completed_at = datetime.utcnow()
        result.duration_seconds = (result.completed_at - started_at).total_seconds()
        return result

    def _get_adapter(self, generator: GeneratorType):
        """Get the appropriate adapter for a generator type."""
        if generator == GeneratorType.FPF:
            return self._fpf_adapter
        elif generator == GeneratorType.GPTR:
            return self._gptr_adapter
        elif generator == GeneratorType.DR:
            return self._dr_adapter
        elif generator == GeneratorType.AIQ:
            return self._aiq_adapter
        raise ValueError(f"Unknown generator type: {generator}")

    def _parse_eval_criteria(self) -> list[EvaluationCriterion]:
        """Parse eval criteria YAML using the canonical strict parser."""
        if not self.config.eval_criteria:
            return []
        return parse_criteria_yaml(self.config.eval_criteria)

    def _build_pairwise_evaluator(
        self,
        *,
        top_n: Optional[int],
        phase_label: str,
    ) -> PairwiseEvaluator:
        """Build a pairwise evaluator with the canonical criteria parsing path."""
        pairwise_config = PairwiseConfig(
            iterations=self.config.eval_iterations,
            judge_models=self.config.eval_judge_models,
            top_n=top_n,
            temperature=self.config.eval_temperature,
            max_tokens=self.config.eval_max_tokens,
            thinking_budget_tokens=self.config.eval_thinking_budget_tokens,
            retries=self.config.eval_retries,
            custom_instructions=self.config.pairwise_eval_instructions,
            concurrent_limit=self.config.eval_concurrency,
            timeout_seconds=self.config.eval_timeout,
            key_mode=self.config.key_mode,
        )
        self.logger.info(
            "Pipeline [%s]: Building %s pairwise evaluator with timeout=%ss retries=%s temperature=%s max_tokens=%s thinking_budget_tokens=%s iterations=%s concurrent_limit=%s top_n=%s key_mode=%s",
            self.source_doc_name,
            phase_label,
            self.config.eval_timeout,
            self.config.eval_retries,
            self.config.eval_temperature,
            self.config.eval_max_tokens,
            self.config.eval_thinking_budget_tokens,
            self.config.eval_iterations,
            self.config.eval_concurrency,
            top_n,
            self.config.key_mode,
        )

        criteria_manager = CriteriaManager()
        if self.config.eval_criteria:
            parsed_criteria = self._parse_eval_criteria()
            if parsed_criteria:
                criteria_manager.set_criteria(parsed_criteria)

        return PairwiseEvaluator(
            pairwise_config,
            criteria_manager=criteria_manager,
            stats_tracker=self.stats,
            user_uuid=self.config.user_uuid,
            run_id=self.run_id,
        )

    def _apply_pairwise_deviations(
        self,
        summary: PairwiseSummary,
        *,
        phase_label: str,
    ) -> None:
        """Attach pairwise deviations consistently across pairwise phases."""
        if not summary.results:
            return

        summary.deviations_by_judge = PairwiseSummary.calculate_deviations(summary.results)
        self.logger.info(
            f"Pipeline [{self.source_doc_name}]: Calculated {phase_label} deviations for "
            f"{len(summary.deviations_by_judge)} judges"
        )

    async def _record_degraded_phase(
        self,
        result: SourceDocResult,
        *,
        phase: str,
        event_type: str,
        description: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a loud degraded-phase signal without aborting the pipeline."""
        self.logger.warning(f"Pipeline [{self.source_doc_name}]: {description}")
        result.errors.append(description)
        if self._log_writer:
            await self._log_writer.event("apicostx", "WARNING", event_type, description)
        await self._emit_timeline_event(
            phase=phase,
            event_type=event_type,
            description=description,
            timestamp=datetime.utcnow(),
            success=False,
            details=details or {},
        )

    def _build_single_eval_degraded_signal(
        self,
        summary: SingleEvalSummary,
        *,
        doc_id: str,
    ) -> Optional[tuple[str, Dict[str, Any]]]:
        """Describe missing judge coverage after retries for one generated doc."""
        expected = int(
            getattr(summary, "expected_evaluations", 0)
            or (self.config.eval_iterations * len(self.config.eval_judge_models or []))
        )
        actual = int(getattr(summary, "num_evaluations", 0) or 0)
        missing_by_judge = dict(getattr(summary, "missing_evaluations_by_judge", {}) or {})
        if expected <= 0 or actual >= expected:
            return None

        iterations = max(int(self.config.eval_iterations or 1), 1)
        if missing_by_judge:
            judge_parts = []
            for judge_model, missing_count in sorted(missing_by_judge.items()):
                if missing_count >= iterations:
                    judge_parts.append(f"{judge_model} (missing all {missing_count}/{iterations} evals)")
                else:
                    judge_parts.append(f"{judge_model} (missing {missing_count}/{iterations} evals)")
            missing_detail = "; ".join(judge_parts)
        else:
            missing_detail = "one or more judge evaluations did not survive retries"

        description = (
            f"Single evaluation degraded for {doc_id}: only {actual}/{expected} judge evaluations succeeded. "
            f"Missing after retries: {missing_detail}. Winner selection will use available judge scores."
        )
        details = {
            "doc_id": doc_id,
            "expected_evaluations": expected,
            "actual_evaluations": actual,
            "failed_evaluations": max(expected - actual, 0),
            "expected_judge_models": list(getattr(summary, "expected_judge_models", []) or []),
            "successful_judge_models": list(getattr(summary, "successful_judge_models", []) or []),
            "missing_judge_models": list(getattr(summary, "missing_judge_models", []) or []),
            "missing_evaluations_by_judge": missing_by_judge,
            "winner_uses_available_scores": True,
        }
        return description, details

    @staticmethod
    def _row_value(row: Any, key: str, default: Any = None) -> Any:
        if isinstance(row, dict):
            return row.get(key, default)
        return getattr(row, key, default)

    @staticmethod
    def _safe_doc_fragment(doc_id: str) -> str:
        return doc_id.replace(":", "_").replace("/", "_").replace("\\", "_")

    @staticmethod
    def _normalized_doc_id_list(raw: Any) -> list[str]:
        if isinstance(raw, str):
            return [part.strip() for part in raw.split(",") if part.strip()]
        if isinstance(raw, (list, tuple, set)):
            return [str(part).strip() for part in raw if str(part).strip()]
        return []

    def _pairwise_checkpoint_model_name(self, comparison_type: str) -> str:
        return f"{comparison_type}_summary"

    def _pairwise_checkpoint_output_ref(self, comparison_type: str) -> str:
        return f"pairwise/{comparison_type}/summary.json"

    def _combine_output_ref(self, doc_id: str) -> str:
        return f"generated/{self._safe_doc_fragment(doc_id)}.md"

    def _rebuild_pairwise_summary_from_rows(
        self,
        rows: list[Any],
        *,
        expected_doc_ids: Optional[list[str]] = None,
    ) -> Optional[PairwiseSummary]:
        if not rows:
            return None

        pairwise_results: list[PairwiseResult] = []
        pair_keys: set[tuple[str, str]] = set()
        elo = EloCalculator()

        for row in sorted(
            rows,
            key=lambda item: self._row_value(item, "compared_at") or datetime.utcnow(),
        ):
            doc_id_a = str(self._row_value(row, "doc_id_a", "") or "")
            doc_id_b = str(self._row_value(row, "doc_id_b", "") or "")
            winner_doc_id = str(self._row_value(row, "winner_doc_id", "") or "")
            judge_model = str(self._row_value(row, "judge_model", "") or "")
            trial = int(self._row_value(row, "trial", 1) or 1)
            reason = str(self._row_value(row, "reason", "") or "")
            compared_at = self._row_value(row, "compared_at")
            if not doc_id_a or not doc_id_b or not winner_doc_id or not judge_model:
                continue
            pair_result = PairwiseResult(
                doc_id_1=doc_id_a,
                doc_id_2=doc_id_b,
                winner_doc_id=winner_doc_id,
                model=judge_model,
                trial=trial,
                reason=reason,
                timestamp=compared_at or datetime.utcnow(),
                completed_at=compared_at,
            )
            pairwise_results.append(pair_result)
            pair_keys.add(tuple(sorted((doc_id_a, doc_id_b))))
            elo.process_result(pair_result)

        if not pairwise_results:
            return None

        if expected_doc_ids is not None:
            expected_doc_id_set = set(self._normalized_doc_id_list(expected_doc_ids))
            actual_doc_id_set: set[str] = set()
            for pair_result in pairwise_results:
                actual_doc_id_set.add(pair_result.doc_id_1)
                actual_doc_id_set.add(pair_result.doc_id_2)
            if expected_doc_id_set != actual_doc_id_set:
                return None

        summary = PairwiseSummary(
            total_comparisons=len(pairwise_results),
            total_pairs=len(pair_keys),
            results=pairwise_results,
            elo_ratings=elo.get_all_ratings(),
            winner_doc_id=elo.get_winner(),
        )
        summary.deviations_by_judge = PairwiseSummary.calculate_deviations(pairwise_results)
        return summary

    async def _load_reusable_pairwise_summary(
        self,
        *,
        comparison_type: str,
        expected_doc_ids: Optional[list[str]] = None,
    ) -> Optional[PairwiseSummary]:
        try:
            async with get_user_session_by_uuid(self.config.user_uuid) as session:
                task_repo = TaskRepository(session)
                checkpoint = await task_repo.find_completed_phase_task(
                    self.run_id,
                    self.source_doc_id,
                    phase="pairwise",
                    model_name=self._pairwise_checkpoint_model_name(comparison_type),
                    iteration=1,
                )
                if checkpoint is None:
                    return None

                results_repo = RunResultsRepository(session)
                rows = list(
                    await results_repo.get_pairwise_results(
                        self.run_id,
                        source_doc_id=self.source_doc_id,
                        comparison_type=comparison_type,
                    )
                )
        except Exception as exc:
            self.logger.debug(
                "Pipeline [%s]: pairwise resume lookup unavailable: %s",
                self.source_doc_name,
                exc,
            )
            return None

        return self._rebuild_pairwise_summary_from_rows(
            rows,
            expected_doc_ids=expected_doc_ids,
        )

    async def _clear_nonreusable_pairwise_state(
        self,
        *,
        comparison_type: str,
    ) -> None:
        try:
            async with get_user_session_by_uuid(self.config.user_uuid) as session:
                results_repo = RunResultsRepository(session)
                await results_repo.delete_pairwise_results(
                    self.run_id,
                    source_doc_id=self.source_doc_id,
                    comparison_type=comparison_type,
                )
                task_repo = TaskRepository(session)
                await task_repo.delete_phase_tasks(
                    self.run_id,
                    self.source_doc_id,
                    phase="pairwise",
                    model_name=self._pairwise_checkpoint_model_name(comparison_type),
                    iteration=1,
                )
        except Exception as exc:
            self.logger.debug(
                "Pipeline [%s]: pairwise resume cleanup unavailable: %s",
                self.source_doc_name,
                exc,
            )

    async def _load_reusable_combined_docs(
        self,
        *,
        expected_input_doc_ids: Optional[list[str]] = None,
    ) -> Dict[str, GeneratedDocument]:
        try:
            async with get_user_session_by_uuid(self.config.user_uuid) as session:
                results_repo = RunResultsRepository(session)
                rows = list(
                    await results_repo.get_combined_docs(
                        self.run_id,
                        source_doc_id=self.source_doc_id,
                    )
                )
        except Exception as exc:
            self.logger.debug(
                "Pipeline [%s]: combine resume lookup unavailable: %s",
                self.source_doc_name,
                exc,
            )
            return {}

        reusable: Dict[str, GeneratedDocument] = {}
        expected_input_doc_ids = self._normalized_doc_id_list(expected_input_doc_ids)
        rows_by_model: Dict[str, list[Any]] = {}
        for row in rows:
            combine_model = str(self._row_value(row, "combine_model", "") or "")
            if not combine_model:
                continue
            rows_by_model.setdefault(combine_model, []).append(row)

        for combine_model, model_rows in rows_by_model.items():
            candidate_rows = model_rows
            if expected_input_doc_ids:
                candidate_rows = [
                    row
                    for row in model_rows
                    if self._normalized_doc_id_list(self._row_value(row, "input_doc_ids"))
                    == expected_input_doc_ids
                ]
                if not candidate_rows:
                    self.logger.info(
                        "Pipeline [%s]: skipping stale combined artifact for %s because inputs changed",
                        self.source_doc_name,
                        combine_model,
                    )
                    await self._clear_stale_combined_artifact(combine_model)
                    continue

            row = max(
                candidate_rows,
                key=lambda item: self._row_value(item, "completed_at") or datetime.min,
            )
            file_path = self._row_value(row, "file_path")
            if not isinstance(file_path, str) or not file_path.strip():
                continue
            file_path_obj = Path(file_path)
            if not file_path_obj.exists():
                self.logger.warning(
                    "Pipeline [%s]: skipping stale combined artifact for %s because file is missing: %s",
                    self.source_doc_name,
                    combine_model,
                    file_path,
                )
                await self._clear_stale_combined_artifact(combine_model)
                continue
            try:
                content = file_path_obj.read_text(encoding="utf-8")
            except Exception as exc:
                self.logger.warning(
                    "Pipeline [%s]: failed to read persisted combined artifact for %s: %s",
                    self.source_doc_name,
                    combine_model,
                    exc,
                )
                await self._clear_stale_combined_artifact(combine_model)
                continue

            reusable[combine_model] = GeneratedDocument(
                doc_id=str(self._row_value(row, "doc_id", "") or ""),
                content=content,
                generator=GeneratorType.FPF,
                model=combine_model,
                source_doc_id=self.source_doc_id,
                iteration=1,
                duration_seconds=float(self._row_value(row, "duration_seconds", 0.0) or 0.0),
                started_at=self._row_value(row, "started_at"),
                completed_at=self._row_value(row, "completed_at"),
            )

        return reusable

    async def _clear_stale_combined_artifact(self, combine_model: str) -> None:
        try:
            async with get_user_session_by_uuid(self.config.user_uuid) as session:
                results_repo = RunResultsRepository(session)
                await results_repo.delete_combined_docs(
                    self.run_id,
                    source_doc_id=self.source_doc_id,
                    combine_model=combine_model,
                )
                task_repo = TaskRepository(session)
                await task_repo.delete_phase_tasks(
                    self.run_id,
                    self.source_doc_id,
                    phase="combine",
                    model_name=combine_model,
                )
        except Exception as exc:
            self.logger.debug(
                "Pipeline [%s]: stale combine cleanup unavailable: %s",
                self.source_doc_name,
                exc,
            )

    async def _persist_terminal_source_doc_status(self, result: SourceDocResult) -> SourceDocResult:
        if result.completed_at is None:
            return result
        try:
            await write_source_doc_status(
                self.run_id,
                self.config.user_uuid,
                result=result,
            )
        except Exception as exc:
            self.logger.warning(
                "Pipeline [%s]: failed to persist source-doc status: %s",
                self.source_doc_name,
                exc,
            )
        return result

    async def run(self) -> SourceDocResult:
        """
        Execute the full pipeline for this source document.

        Returns:
            SourceDocResult containing all results for this document
        """
        started_at = datetime.utcnow()

        result = SourceDocResult(
            source_doc_id=self.source_doc_id,
            source_doc_name=self.source_doc_name,
            status=RunPhase.GENERATING,
            generated_docs=[],
            single_eval_results={},
            pairwise_results=None,
            winner_doc_id=None,
            combined_doc=None,
            post_combine_eval_results=None,
            timeline_events=[],
            errors=[],
            locked_invariant_failures=self._locked_invariant_failures,
            duration_seconds=0.0,
            started_at=started_at,
            completed_at=None,
        )

        try:
            # Phase 1: Generation with streaming single eval
            self.logger.info(f"Pipeline [{self.source_doc_name}]: Starting generation phase")
            if self._log_writer:
                _doc_idx = (self.config.document_ids or []).index(self.source_doc_id) + 1 if self.source_doc_id in (self.config.document_ids or []) else 0
                _doc_total = len(self.config.document_ids or [])
                await self._log_writer.event("apicostx", "INFO", "doc_gen_start",
                                             f"Doc {_doc_idx}/{_doc_total}: generation phase starting")
            await self._run_generation_with_eval(result)

            if self._stop_requested():
                return await self._persist_terminal_source_doc_status(
                    self._mark_result_stopped(result, started_at=started_at)
                )

            # Check if we have any successful generations
            if not result.generated_docs:
                result.status = RunPhase.FAILED
                result.errors.append("No documents were generated successfully")
                result.completed_at = datetime.utcnow()
                result.duration_seconds = (result.completed_at - started_at).total_seconds()
                return await self._persist_terminal_source_doc_status(result)

            # Calculate deviations after all single evals complete
            if result.single_eval_results and len(result.single_eval_results) > 0:
                from ..evaluation.single_doc import SingleEvalSummary
                deviations = SingleEvalSummary.calculate_deviations(result.single_eval_results)
                # Attach deviations to each summary
                for doc_id, summary in result.single_eval_results.items():
                    summary.deviations_by_judge_criterion = deviations
                self.logger.info(f"Pipeline [{self.source_doc_name}]: Calculated deviations for {len(deviations)} judges")

            # Phase 2: Pairwise evaluation
            if self.config.enable_pairwise and len(result.generated_docs) >= 2:
                result.status = RunPhase.PAIRWISE_EVAL
                self.logger.info(f"Pipeline [{self.source_doc_name}]: Starting pairwise phase with {len(result.generated_docs)} docs")
                if self._log_writer:
                    _doc_idx = (self.config.document_ids or []).index(self.source_doc_id) + 1 if self.source_doc_id in (self.config.document_ids or []) else 0
                    _doc_total = len(self.config.document_ids or [])
                    await self._log_writer.event("apicostx", "INFO", "doc_eval_start",
                                                 f"Doc {_doc_idx}/{_doc_total}: pairwise evaluation starting")
                await self._run_pairwise(result)

                if self._stop_requested():
                    return await self._persist_terminal_source_doc_status(
                        self._mark_result_stopped(result, started_at=started_at)
                    )

            # Determine winner from single eval if pairwise was disabled
            if not result.winner_doc_id and result.single_eval_results:
                doc_scores = {}
                for doc_id, summary in result.single_eval_results.items():
                    if hasattr(summary, 'avg_score') and summary.avg_score is not None:
                        doc_scores[doc_id] = summary.avg_score
                if doc_scores:
                    result.winner_doc_id = max(doc_scores, key=doc_scores.get)
                    self.logger.info(f"Pipeline [{self.source_doc_name}]: Winner from single eval: {result.winner_doc_id}")

            # Phase 3: Combine
            if self.config.enable_combine and result.winner_doc_id:
                result.status = RunPhase.COMBINING
                self.logger.info(f"Pipeline [{self.source_doc_name}]: Starting combine phase")
                await self._run_combine(result)

                if self._stop_requested():
                    return await self._persist_terminal_source_doc_status(
                        self._mark_result_stopped(result, started_at=started_at)
                    )

            # Phase 4: Post-combine evaluation
            if self.config.enable_combine and result.combined_docs and self.config.enable_pairwise:
                result.status = RunPhase.POST_COMBINE_EVAL
                self.logger.info(f"Pipeline [{self.source_doc_name}]: Starting post-combine eval")
                await self._run_post_combine_eval(result)

                if self._stop_requested():
                    return await self._persist_terminal_source_doc_status(
                        self._mark_result_stopped(result, started_at=started_at)
                    )

            # === Cascading Winner Determination ===
            # Promote post-combine winner if available (highest priority)
            if result.post_combine_eval_results and result.post_combine_eval_results.winner_doc_id:
                result.winner_doc_id = result.post_combine_eval_results.winner_doc_id
                self.logger.info(
                    f"Pipeline [{self.source_doc_name}]: Winner from post-combine: {result.winner_doc_id}"
                )
            # Fall back to pairwise winner (already set during pairwise phase if available)
            elif not result.winner_doc_id and result.pairwise_results and result.pairwise_results.winner_doc_id:
                result.winner_doc_id = result.pairwise_results.winner_doc_id
                self.logger.info(
                    f"Pipeline [{self.source_doc_name}]: Winner from pairwise: {result.winner_doc_id}"
                )

            # Mark as complete
            result.status = (
                RunPhase.COMPLETED_WITH_ERRORS
                if result.errors
                else RunPhase.COMPLETED
            )
            result.completed_at = datetime.utcnow()
            result.duration_seconds = (result.completed_at - started_at).total_seconds()

            self.logger.info(
                f"Pipeline [{self.source_doc_name}]: Completed | "
                f"docs={len(result.generated_docs)} "
                f"winner={result.winner_doc_id}"
            )
            if self._log_writer:
                _doc_idx = (self.config.document_ids or []).index(self.source_doc_id) + 1 if self.source_doc_id in (self.config.document_ids or []) else 0
                _doc_total = len(self.config.document_ids or [])
                await self._log_writer.event("apicostx", "INFO", "doc_complete",
                                             f"Doc {_doc_idx}/{_doc_total}: pipeline complete, duration={result.duration_seconds:.1f}s")

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self.logger.error(f"Pipeline [{self.source_doc_name}] failed: {e}\n{tb}")
            if self._log_writer:
                _doc_idx = (self.config.document_ids or []).index(self.source_doc_id) + 1 if self.source_doc_id in (self.config.document_ids or []) else 0
                _doc_total = len(self.config.document_ids or [])
                await self._log_writer.event("apicostx", "ERROR", "doc_failed",
                                             f"Doc {_doc_idx}/{_doc_total}: pipeline failed, error_type={type(e).__name__}")
                await self._log_writer.detail("apicostx", "ERROR", "Pipeline failure traceback",
                                              payload={"doc_id": self.source_doc_id, "traceback": tb})
            result.status = RunPhase.FAILED
            result.errors.append(str(e))
            result.errors.append(tb)
            result.completed_at = datetime.utcnow()
            result.duration_seconds = (result.completed_at - started_at).total_seconds()

        return await self._persist_terminal_source_doc_status(result)

    async def _emit_timeline_event(
        self,
        phase: str,
        event_type: str,
        description: str,
        model: Optional[str] = None,
        timestamp: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        duration_seconds: Optional[float] = None,
        success: bool = True,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit a timeline event for this source document."""
        event = {
            "source_doc_id": self.source_doc_id,
            "source_doc_name": self.source_doc_name,
            "phase": phase,
            "event_type": event_type,
            "description": description,
            "model": model,
            "timestamp": (timestamp or datetime.utcnow()).isoformat(),
            "completed_at": completed_at.isoformat() if completed_at else None,
            "duration_seconds": duration_seconds,
            "success": success,
            "details": details or {},
        }

        # Call the timeline event callback if provided
        if self.on_timeline_event:
            try:
                await self.on_timeline_event(self.run_id, event)
            except Exception as e:
                self.logger.warning(f"Timeline event callback failed: {e}")

    async def _save_generated_content(self, gen_doc: GeneratedDocument) -> Optional[str]:
        """Save generated document content to a file for later retrieval.

        Files are stored in data/user_{user_uuid}/runs/{run_id}/generated/{doc_id}.md

        Returns the absolute path of the file written (as str), or raises on failure.
        """
        import aiofiles

        try:
            # Validate content before saving
            if not gen_doc.content:
                raise ValueError(f"Cannot save document {gen_doc.doc_id}: content is None or empty")

            if not gen_doc.content.strip():
                raise ValueError(f"Cannot save document {gen_doc.doc_id}: content is only whitespace")

            # Create directory structure
            gen_dir = self._get_run_root() / "generated"
            gen_dir.mkdir(parents=True, exist_ok=True)

            # Sanitize doc_id for filename (replace invalid chars)
            safe_doc_id = gen_doc.doc_id.replace(':', '_').replace('/', '_').replace('\\', '_')
            file_path = gen_dir / f"{safe_doc_id}.md"

            # Write content
            async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:  # law-exempt: writing Markdown content file, not a JSON results blob
                await f.write(gen_doc.content)

            self.logger.debug(f"Pipeline [{self.source_doc_name}]: Saved generated content to {file_path}")
            return str(file_path)
        except Exception as e:
            self.logger.error(f"Pipeline [{self.source_doc_name}]: Failed to save generated content for {gen_doc.doc_id}: {e}")
            raise RuntimeError(f"Failed to save {gen_doc.doc_id}: {e}") from e

    async def _run_generation_with_eval(self, result: SourceDocResult) -> None:
        """
        Generate variations for THIS source doc only, with streaming single eval.

        Each document is evaluated IMMEDIATELY after generation, not waiting
        for other documents to complete.
        """
        # Build task list: (generator, model, iteration)
        tasks = []
        for generator in self.config.generators:
            generator_models = self.config.get_models_for_generator(generator)
            for model in generator_models:
                for iteration in range(1, self.config.iterations + 1):
                    tasks.append((generator, model, iteration))

        total_tasks = len(tasks)
        completed = 0

        # Initialize run_store tasks list so WebSocket clients see initial progress
        if self.run_store:
            try:
                initial_tasks = []
                for generator, model, iteration in tasks:
                    task_id = f"{self.source_doc_id}.{generator.value}.{iteration}.{model}"
                    initial_tasks.append({
                        "id": task_id,
                        "document_id": self.source_doc_id,
                        "document_name": self.source_doc_name,
                        "generator": generator.value,
                        "model": model,
                        "iteration": iteration,
                        "status": "pending",
                        "progress": 0.0,
                        "message": None,
                        "score": None,
                        "duration_seconds": 0.0,
                        "started_at": None,
                        "completed_at": None,
                        "error_message": None,
                    })
                # Append to existing tasks (multi-doc has multiple source docs)
                existing_run = self.run_store.get(self.run_id)
                if existing_run and 'tasks' in existing_run:
                    all_tasks = existing_run['tasks'] + initial_tasks
                else:
                    all_tasks = initial_tasks
                self.run_store.update(self.run_id, tasks=all_tasks)
            except Exception as e:
                self.logger.warning(f"Pipeline [{self.source_doc_name}]: Failed to initialize run_store tasks: {e}")

        # Setup single-doc evaluator if enabled
        single_evaluator = None
        if self.config.enable_single_eval:
            eval_config = SingleEvalConfig(
                iterations=self.config.eval_iterations,
                judge_models=self.config.eval_judge_models,
                custom_instructions=self.config.single_eval_instructions,
                custom_criteria=self.config.eval_criteria,
                concurrent_limit=self.config.eval_concurrency,
                timeout_seconds=self.config.eval_timeout,
                temperature=self.config.eval_temperature,
                max_tokens=self.config.eval_max_tokens,
                retries=self.config.eval_retries,
                key_mode=self.config.key_mode,
            )
            single_evaluator = SingleDocEvaluator(
                eval_config,
                stats_tracker=self.stats,
                user_uuid=self.config.user_uuid,
                run_id=self.run_id,
            )

        async def process_task(task_info):
            nonlocal completed
            generator, model, iteration = task_info

            acquired = await self._acquire_semaphore_or_stop(self.semaphore)
            if not acquired:
                return

            try:
                if self._stop_requested():
                    return

                # Resume: check if this task already completed in a previous run
                _cache_key = f"{self.source_doc_id}:{model}:{iteration}"
                _cached = self.config.completed_generation_cache.get(_cache_key)
                _from_cache = False
                gen_result = None

                if _cached:
                    _run_root = self._get_run_root()
                    _out = _run_root / (_cached.get("output_ref") or f"generated/{_cached.get('doc_id', '')}.md")
                    if _out.exists() and _out.stat().st_size > 0:
                        try:
                            _gt = generator
                            for _g in GeneratorType:
                                if _g.value == _cached.get("generator"):
                                    _gt = _g
                                    break
                            gen_result = GeneratedDocument(
                                doc_id=_cached["doc_id"],
                                content=_out.read_text(encoding="utf-8"),
                                generator=_gt,
                                model=model,
                                source_doc_id=self.source_doc_id,
                                iteration=iteration,
                            )
                            _from_cache = True
                            self.logger.info(
                                f"Pipeline [{self.source_doc_name}]: Resume — skipping completed"
                                f" task {_cache_key}"
                            )
                        except Exception as _e:
                            self.logger.warning(
                                f"Pipeline [{self.source_doc_name}]: Cannot load cached output for"
                                f" {_cache_key}: {_e} — re-generating"
                            )

                # Create unique task ID
                task_id = f"{self.source_doc_id}.{generator.value}.{iteration}.{model}"

                if not _from_cache:
                    # Update run_store: mark task as running
                    if self.run_store:
                        try:
                            run = self.run_store.get(self.run_id)
                            if run and 'tasks' in run:
                                tasks_list = run['tasks']
                                for t in tasks_list:
                                    if t['id'] == task_id:
                                        t['status'] = 'running'
                                        t['progress'] = 0.05
                                        t['message'] = 'started'
                                        t['started_at'] = datetime.utcnow()
                                        break
                                self.run_store.update(self.run_id, tasks=tasks_list)
                        except Exception as e:
                            self.logger.warning(f"Failed to update run_store task status: {e}")

                    # Create progress callback for this task
                    async def _progress_callback(stage: str, progress: float, message: Optional[str]):
                        """Update task progress in run_store and broadcast via WebSocket."""
                        if self.run_store:
                            try:
                                run = self.run_store.get(self.run_id)
                                if run and 'tasks' in run:
                                    tasks_list = run['tasks']
                                    for tt in tasks_list:
                                        if tt['id'] == task_id:
                                            tt['progress'] = progress
                                            tt['message'] = message
                                            break
                                    self.run_store.update(self.run_id, tasks=tasks_list)
                            except Exception:
                                pass

                    # 1. Generate
                    gen_result = await self._generate_single(
                        generator=generator,
                        model=model,
                        iteration=iteration,
                        task_id=task_id,
                        progress_callback=_progress_callback,
                    )

                if gen_result:
                    result.generated_docs.append(gen_result)
                    # Resolve or create the on-disk Markdown file for this doc.
                    # Cached docs already have the file; fresh docs are written now.
                    if _from_cache:
                        _gen_dir = self._get_run_root() / "generated"
                        _safe = gen_result.doc_id.replace(':', '_').replace('/', '_').replace('\\', '_')
                        _saved_file_path: Optional[str] = str(_gen_dir / f"{_safe}.md")
                    else:
                        # _save_generated_content now returns the path it wrote.
                        _saved_file_path = await self._save_generated_content(gen_result)

                    # For cached (resumed) tasks, fire the lightweight counter callback instead
                    if _from_cache and self.config.on_gen_cached:
                        try:
                            await self.config.on_gen_cached(
                                gen_result.doc_id,
                                model,
                                generator.value,
                                self.source_doc_id,
                                iteration,
                            )
                        except Exception as _e:
                            self.logger.warning(
                                f"Pipeline [{self.source_doc_name}]: on_gen_cached failed: {_e}"
                            )

                    # Fire on_gen_complete callback to persist to run_generated_docs.
                    # Always called — even for cached docs — so any DB row missing due to
                    # a prior crash is healed here (write_gen_doc uses ON CONFLICT DO NOTHING).
                    # Exception is NOT swallowed: a DB write failure must surface as a task
                    # failure so the run is not silently incomplete.
                    if self.config.on_gen_complete:
                        self.logger.info(f"Pipeline [{self.source_doc_name}]: Calling on_gen_complete for {gen_result.doc_id}")
                        await self.config.on_gen_complete(
                            gen_result.doc_id,
                            model,
                            generator.value,
                            self.source_doc_id,  # source_doc_id
                            iteration,
                            _saved_file_path,    # fix-1: pass file path so DB row is populated
                            duration_seconds=gen_result.duration_seconds,
                            started_at=gen_result.started_at,
                        )
                        self.logger.info(f"Pipeline [{self.source_doc_name}]: on_gen_complete succeeded for {gen_result.doc_id}")

                    # Emit generation timeline event
                    await self._emit_timeline_event(
                        phase="generation",
                        event_type="generation",
                        description=f"Generated doc using {generator.value}",
                        model=model,
                        timestamp=gen_result.started_at,
                        completed_at=gen_result.completed_at,
                        duration_seconds=gen_result.duration_seconds,
                        success=True,
                        details={"doc_id": gen_result.doc_id},
                    )

                    # 2. Single eval IMMEDIATELY (streaming)
                    if self._stop_requested():
                        self.logger.info(
                            f"Pipeline [{self.source_doc_name}]: Stop requested after generation "
                            f"for {gen_result.doc_id}; skipping single eval"
                        )
                    elif single_evaluator and gen_result.content:
                        if _from_cache:
                            # Resume backfill: only run the missing judge/trial attempts for this doc.
                            _expected_attempts = {
                                (judge_model, trial)
                                for judge_model in self.config.eval_judge_models
                                for trial in range(1, self.config.eval_iterations + 1)
                            }
                            _done_attempts = self.config.completed_eval_cache.get(gen_result.doc_id, set())
                            _missing_attempts = sorted(_expected_attempts - _done_attempts)
                            if _missing_attempts:
                                _trial_numbers_by_judge: dict[str, list[int]] = {}
                                for _judge_model, _trial in _missing_attempts:
                                    _trial_numbers_by_judge.setdefault(_judge_model, []).append(_trial)
                                _judges_to_run = sorted(_trial_numbers_by_judge)
                                self.logger.info(
                                    f"Pipeline [{self.source_doc_name}]: Backfilling {len(_missing_attempts)} "
                                    f"missing eval attempt(s) for cached doc {gen_result.doc_id}: "
                                    f"{_trial_numbers_by_judge}"
                                )
                                _partial_config = SingleEvalConfig(
                                    iterations=self.config.eval_iterations,
                                    judge_models=_judges_to_run,
                                    custom_instructions=self.config.single_eval_instructions,
                                    custom_criteria=self.config.eval_criteria,
                                    concurrent_limit=self.config.eval_concurrency,
                                    timeout_seconds=self.config.eval_timeout,
                                    temperature=self.config.eval_temperature,
                                    max_tokens=self.config.eval_max_tokens,
                                    retries=self.config.eval_retries,
                                    key_mode=self.config.key_mode,
                                    trial_numbers_by_judge=_trial_numbers_by_judge,
                                )
                                _partial_evaluator = SingleDocEvaluator(
                                    _partial_config,
                                    stats_tracker=self.stats,
                                    user_uuid=self.config.user_uuid,
                                    run_id=self.run_id,
                                )
                                try:
                                    _eval_input = DocumentInput(
                                        doc_id=gen_result.doc_id,
                                        content=gen_result.content,
                                    )
                                    # Fire on_eval_complete per judge — persists to DB via incremental callback.
                                    # Do NOT set result.single_eval_results so the incremental-data path
                                    # (which holds pre-existing scores) is preserved at run completion.
                                    _backfill_summary = await _partial_evaluator.evaluate_document(
                                        _eval_input,
                                        on_eval_complete=self.config.on_eval_complete,
                                    )
                                    _degraded_signal = self._build_single_eval_degraded_signal(
                                        _backfill_summary,
                                        doc_id=gen_result.doc_id,
                                    )
                                    if _degraded_signal:
                                        _description, _details = _degraded_signal
                                        await self._record_degraded_phase(
                                            result,
                                            phase="evaluation",
                                            event_type="single_eval_degraded",
                                            description=_description,
                                            details=_details,
                                        )
                                except Exception as e:
                                    self.logger.error(
                                        f"Pipeline [{self.source_doc_name}]: Backfill eval failed "
                                        f"for {gen_result.doc_id}: {e}"
                                    )
                                    result.errors.append(f"Backfill eval failed for {gen_result.doc_id}: {e}")
                            else:
                                self.logger.info(
                                    f"Pipeline [{self.source_doc_name}]: All eval attempts already scored "
                                    f"cached doc {gen_result.doc_id} — skipping eval"
                                )
                        else:
                            try:
                                eval_input = DocumentInput(
                                    doc_id=gen_result.doc_id,
                                    content=gen_result.content,
                                )
                                eval_started_at = datetime.utcnow()
                                summary = await single_evaluator.evaluate_document(
                                    eval_input,
                                    on_eval_complete=self.config.on_eval_complete,
                                )
                                result.single_eval_results[gen_result.doc_id] = summary
                                eval_completed_at = datetime.utcnow()

                                degraded_signal = self._build_single_eval_degraded_signal(
                                    summary,
                                    doc_id=gen_result.doc_id,
                                )
                                if degraded_signal:
                                    description, details = degraded_signal
                                    await self._record_degraded_phase(
                                        result,
                                        phase="evaluation",
                                        event_type="single_eval_degraded",
                                        description=description,
                                        details=details,
                                    )

                                # Emit single eval timeline event
                                await self._emit_timeline_event(
                                    phase="evaluation",
                                    event_type="single_eval",
                                    description=f"Evaluated {gen_result.doc_id[:20]}...",
                                    model=", ".join(self.config.eval_judge_models) if self.config.eval_judge_models else None,
                                    timestamp=eval_started_at,
                                    completed_at=eval_completed_at,
                                    duration_seconds=(eval_completed_at - eval_started_at).total_seconds(),
                                    success=True,
                                    details={
                                        "doc_id": gen_result.doc_id,
                                        "average_score": summary.avg_score,
                                    },
                                )

                                self.logger.info(
                                    f"Pipeline [{self.source_doc_name}]: Single eval complete: {gen_result.doc_id} | "
                                    f"avg={summary.avg_score:.2f}"
                                )
                            except Exception as e:
                                self.logger.error(f"Pipeline [{self.source_doc_name}]: Single eval failed for {gen_result.doc_id}: {e}")
                                result.errors.append(f"Single eval failed for {gen_result.doc_id}: {e}")

                    # Update run_store: mark task completed
                    if self.run_store:
                        try:
                            run = self.run_store.get(self.run_id)
                            if run and 'tasks' in run:
                                tasks_list = run['tasks']
                                for t in tasks_list:
                                    if t['id'] == task_id:
                                        t['status'] = 'completed'
                                        t['progress'] = 1.0
                                        t['message'] = 'completed'
                                        t['duration_seconds'] = gen_result.duration_seconds or 0.0
                                        t['completed_at'] = datetime.utcnow()
                                        break
                                self.run_store.update(self.run_id, tasks=tasks_list)
                        except Exception as e:
                            self.logger.warning(f"Failed to update run_store task completion: {e}")

                completed += 1
                if self.config.on_progress:
                    self.config.on_progress(
                        "generating",
                        completed / total_tasks,
                        f"[{self.source_doc_name}] Generated {completed}/{total_tasks}",
                    )
            finally:
                self.semaphore.release()

        # Run all tasks - return_exceptions=True ensures one failure doesn't abort others
        task_results = await asyncio.gather(*[process_task(t) for t in tasks], return_exceptions=True)

        # Log any exceptions that occurred
        for i, task_result in enumerate(task_results):
            if isinstance(task_result, Exception):
                generator, model, iteration = tasks[i]
                task_id = f"{self.source_doc_id}.{generator.value}.{iteration}.{model}"
                self.logger.error(f"Pipeline [{self.source_doc_name}]: Task {task_id} failed: {task_result}")
                result.errors.append(f"Task {task_id} failed: {str(task_result)}")

    async def _generate_single(
        self,
        generator: GeneratorType,
        model: str,
        iteration: int,
        task_id: Optional[str] = None,
        progress_callback: Optional[Callable] = None,
    ) -> Optional[GeneratedDocument]:
        """Generate a single document for this source doc."""
        started_at = datetime.utcnow()
        detail_log_writer = (
            self._log_writer
            if getattr(self._log_writer, "save_to_sidecar", False)
            else None
        )

        # Tag this task's context so SidecarLogHandler only captures its own records
        if detail_log_writer:
            _current_capture_id.set(detail_log_writer.run_id)

        # Track generation start in live stats
        if self.stats:
            self.stats.record_call_start("generation", f"Generating {self.source_doc_id} with {model}")

        try:
            adapter = self._get_adapter(generator)

            settings = self.config.get_model_settings_for_generator(generator, model)
            provider = settings.get("provider")
            base_model = settings.get("model") or (model.split(":", 1)[1] if ":" in model else model)
            temperature = settings.get("temperature")
            max_tokens = settings.get("max_tokens")

            if not provider:
                raise ValueError(f"provider not set for model {model}")
            if max_tokens is None or max_tokens < 1:
                raise ValueError(f"max_tokens missing for model {model}")
            if temperature is None:
                raise ValueError(f"temperature missing for model {model}")

            # Create generation config
            gen_config = GenerationConfig(
                provider=provider,
                model=base_model,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            # Create task ID for tracking
            task_id = task_id or f"{self.source_doc_id}.{generator.value}.{iteration}.{model}"
            phase_config = {}
            if generator == GeneratorType.FPF:
                phase_config = self.config.fpf_config
            elif generator == GeneratorType.GPTR:
                phase_config = self.config.gptr_config
            elif generator == GeneratorType.DR:
                phase_config = self.config.dr_config
            elif generator == GeneratorType.MA:
                phase_config = self.config.ma_config
            elif generator == GeneratorType.AIQ:
                phase_config = self.config.aiq_config
            gen_config.extra = compile_generation_adapter_extra(
                phase_config=phase_config,
                task_id=task_id,
                run_id=self.run_id,
                phase=f"{generator.value}.generate",
                document_id=self.source_doc_id,
                iteration=iteration,
                temperature=temperature,
                max_tokens=max_tokens,
                key_mode=self.config.key_mode,
                request_timeout=self.config.request_timeout,
                fpf_max_retries=self.config.fpf_max_retries,
                fpf_retry_delay=self.config.fpf_retry_delay,
                thinking_budget_tokens=settings.get("thinking_budget_tokens"),
            )

            # Build instructions with optional criteria exposure
            instructions = self.config.instructions
            if self.config.expose_criteria_to_generators and self.config.eval_criteria:
                criteria_header = """

=== EVALUATION CRITERIA (Your output will be judged on these) ===
The following criteria will be used to evaluate your output.
Optimize your response to score highly on each criterion:

"""
                instructions = (instructions or "") + criteria_header + self.config.eval_criteria

            # Generate based on adapter type
            _adapter_source = "fpf" if generator == GeneratorType.FPF else generator.value
            _runtime_details = ""
            if generator == GeneratorType.AIQ:
                _extra = gen_config.extra or {}
                _runtime_details = (
                    f", aiq_soft_timeout={_extra.get('timeout_seconds') or 1800}s"
                    f", aiq_job_expiry={_extra.get('job_expiry_seconds') or 86400}s"
                )
            if self._log_writer:
                await self._log_writer.event(_adapter_source, "INFO", "adapter_start",
                                             f"{generator.value.upper()} run starting: task_id={task_id}, provider={provider}, model={model}{_runtime_details}")
            self._active_adapter_tasks[task_id] = generator
            try:
                if generator == GeneratorType.FPF:
                    if not instructions:
                        raise ValueError("FPF requires instructions")

                    # Apply provider-level rate limiting
                    async with RateLimitedRequest(provider):
                        gen_result = await adapter.generate(
                            query=instructions,
                            config=gen_config,
                            user_uuid=self.config.user_uuid,
                            document_content=self.content,
                            progress_callback=progress_callback,
                            log_writer=detail_log_writer,
                        )
                else:
                    # GPTR/DR/AI-Q use generation instructions plus source-doc content as the research topic
                    query_parts = []
                    if instructions:
                        query_parts.append(instructions)
                    elif generator == GeneratorType.AIQ:
                        raise ValueError("AI-Q requires generation instructions")
                    query_parts.append(self.content)
                    full_query = "\n\n".join(query_parts)

                    async with RateLimitedRequest(provider):
                        _extra_kwargs = {}
                        if detail_log_writer and generator in (GeneratorType.GPTR, GeneratorType.DR, GeneratorType.AIQ):
                            _extra_kwargs["log_writer"] = detail_log_writer
                        gen_result = await adapter.generate(
                            query=full_query,
                            config=gen_config,
                            user_uuid=self.config.user_uuid,
                            progress_callback=progress_callback,
                            **_extra_kwargs,
                        )
            finally:
                self._active_adapter_tasks.pop(task_id, None)

            completed_at = datetime.utcnow()

            if self._log_writer:
                await self._log_writer.event(_adapter_source, "INFO", "adapter_complete",
                                             f"{generator.value.upper()} run completed: task_id={task_id}")

            # Create unique doc ID
            short_doc_id = self.source_doc_id[-8:] if len(self.source_doc_id) >= 8 else self.source_doc_id
            file_uuid = str(uuid4())[:4]
            gen_doc_id = f"{short_doc_id}.{file_uuid}.{generator.value}.{iteration}.{model.replace(':', '_').replace('/', '_')}"

            # Track generation success
            if self.stats:
                self.stats.record_success()

            return GeneratedDocument(
                doc_id=gen_doc_id,
                content=gen_result.content,
                generator=generator,
                model=model,
                source_doc_id=self.source_doc_id,
                iteration=iteration,
                tokens_in=gen_result.input_tokens or None,
                tokens_out=gen_result.output_tokens or None,
                duration_seconds=gen_result.duration_seconds or (completed_at - started_at).total_seconds(),
                started_at=started_at,
                completed_at=completed_at,
                metadata=dict(gen_result.metadata or {}),
            )

        except Exception as e:
            import traceback as _tb
            self.logger.exception(f"Pipeline [{self.source_doc_name}]: Generation failed: {generator} {model}: {e}")
            if isinstance(e, FpfExecutionError) and e.invariant_failure:
                invariant_failure = dict(e.invariant_failure)
                invariant_failure.setdefault("source_doc_id", self.source_doc_id)
                invariant_failure.setdefault("source_doc_name", self.source_doc_name)
                self._locked_invariant_failures.append(invariant_failure)
                self.logger.warning(
                    "Locked invariant failure handed off from FPF: run=%s source_doc_id=%s task_id=%s provider=%s model=%s failure_type=%s",
                    self.run_id[:8],
                    self.source_doc_id,
                    invariant_failure.get("task_id"),
                    invariant_failure.get("provider"),
                    invariant_failure.get("model"),
                    invariant_failure.get("failure_type"),
                )
                if self._log_writer:
                    await self._log_writer.detail(
                        "apicostx",
                        "WARNING",
                        "Locked invariant failure",
                        payload=invariant_failure,
                    )
            if self._log_writer:
                _adapter_source = "fpf" if generator == GeneratorType.FPF else generator.value
                await self._log_writer.event(_adapter_source, "ERROR", "adapter_failed",
                                             f"{generator.value.upper()} failed: task_id={task_id}, error_type={type(e).__name__}")
                await self._log_writer.detail(_adapter_source, "ERROR", f"{generator.value.upper()} execution failed",
                                              payload={"traceback": _tb.format_exc()})
            if self.stats:
                self.stats.record_failure(str(e))
            return None

    async def _run_pairwise(self, result: SourceDocResult) -> None:
        """
        Run pairwise evaluation for THIS source doc's variations only.

        Only compares documents generated from this source document.
        """
        if self._stop_requested():
            self.logger.info(f"Pipeline [{self.source_doc_name}]: Pairwise skipped - stop requested")
            return

        # Filter out empty content
        valid_docs = [
            doc for doc in result.generated_docs
            if doc.content and len(doc.content.strip()) > 0
        ]

        if len(valid_docs) < 2:
            await self._record_degraded_phase(
                result,
                phase="pairwise",
                event_type="pairwise_skipped",
                description=(
                    f"Pairwise skipped: need at least 2 valid documents, found {len(valid_docs)}. "
                    "Continuing with the rest of the pipeline."
                ),
                details={"valid_doc_count": len(valid_docs)},
            )
            return

        doc_ids = [doc.doc_id for doc in valid_docs]
        contents = {doc.doc_id: doc.content for doc in valid_docs}

        cached_summary = await self._load_reusable_pairwise_summary(
            comparison_type="pre_combine",
            expected_doc_ids=doc_ids,
        )
        if cached_summary is not None:
            result.pairwise_results = cached_summary
            result.winner_doc_id = cached_summary.winner_doc_id
            self.logger.info(
                "Pipeline [%s]: Reused completed pairwise phase from durable checkpoints",
                self.source_doc_name,
            )
            return

        await self._clear_nonreusable_pairwise_state(comparison_type="pre_combine")

        evaluator = self._build_pairwise_evaluator(
            top_n=self.config.pairwise_top_n,
            phase_label="pairwise",
        )

        # Get single-eval scores for top-N filtering.
        # Always pull from both sources so resumed runs (where cached docs are
        # absent from result.single_eval_results) get the correct selection.
        if self.config.pairwise_top_n:
            scores: Dict[str, float] = {}
            # 1. Fresh in-memory scores from the current session
            for doc_id, summary in (result.single_eval_results or {}).items():
                if summary.avg_score is not None:
                    scores[doc_id] = summary.avg_score
            # 2. Incremental scores (covers cached/resumed docs) — fill gaps only
            if self.config.get_all_eval_scores:
                for doc_id, score in self.config.get_all_eval_scores().items():
                    if doc_id not in scores:
                        scores[doc_id] = score
            if scores:
                doc_ids = evaluator.filter_top_n(doc_ids, scores, self.config.pairwise_top_n)
                contents = {d: contents[d] for d in doc_ids}
                self.logger.info(f"Pipeline [{self.source_doc_name}]: Filtered to top {len(doc_ids)} docs for pairwise (scores available for {len(scores)} docs)")

        # Run pairwise
        pairwise_started_at = datetime.utcnow()
        expected_comparisons = (
            len(list(itertools.combinations(doc_ids, 2)))
            * self.config.eval_iterations
            * len(self.config.eval_judge_models or [])
        )
        summary = await evaluator.evaluate_all_pairs(doc_ids, contents)
        pairwise_completed_at = datetime.utcnow()
        self._apply_pairwise_deviations(summary, phase_label="pairwise")

        degraded = summary.total_comparisons < expected_comparisons
        if summary.results:
            try:
                await write_pairwise_results(
                    self.run_id,
                    self.config.user_uuid,
                    source_doc_id=self.source_doc_id,
                    summary=summary,
                    comparison_type="pre_combine",
                )
                if not degraded:
                    await mark_phase_checkpoint_completed(
                        self.run_id,
                        self.config.user_uuid,
                        source_doc_id=self.source_doc_id,
                        phase="pairwise",
                        model_name=self._pairwise_checkpoint_model_name("pre_combine"),
                        iteration=1,
                        output_ref=self._pairwise_checkpoint_output_ref("pre_combine"),
                    )
                else:
                    await self._clear_nonreusable_pairwise_state(comparison_type="pre_combine")
            except Exception as exc:
                self.logger.warning(
                    "Pipeline [%s]: pairwise checkpoint persistence failed (non-fatal): %s",
                    self.source_doc_name,
                    exc,
                )

        if summary.total_comparisons == 0:
            await self._record_degraded_phase(
                result,
                phase="pairwise",
                event_type="pairwise_failed",
                description=(
                    "Pairwise produced no successful comparisons after retries. "
                    "Continuing with the rest of the pipeline."
                ),
                details={
                    "expected_comparisons": expected_comparisons,
                    "successful_comparisons": summary.total_comparisons,
                },
            )
        elif degraded:
            await self._record_degraded_phase(
                result,
                phase="pairwise",
                event_type="pairwise_degraded",
                description=(
                    f"Pairwise completed partially: {summary.total_comparisons} of "
                    f"{expected_comparisons} expected comparisons succeeded after retries."
                ),
                details={
                    "expected_comparisons": expected_comparisons,
                    "successful_comparisons": summary.total_comparisons,
                },
            )

        result.pairwise_results = summary
        result.winner_doc_id = summary.winner_doc_id

        # Emit pairwise timeline event
        await self._emit_timeline_event(
            phase="pairwise",
            event_type="pairwise_eval",
            description=f"Pairwise evaluation: {summary.total_comparisons} comparisons",
            model=", ".join(self.config.eval_judge_models) if self.config.eval_judge_models else None,
            timestamp=pairwise_started_at,
            completed_at=pairwise_completed_at,
            duration_seconds=(pairwise_completed_at - pairwise_started_at).total_seconds(),
            success=not degraded and summary.total_comparisons > 0,
            details={
                "total_comparisons": summary.total_comparisons,
                "expected_comparisons": expected_comparisons,
                "winner_doc_id": summary.winner_doc_id,
                "degraded": degraded or summary.total_comparisons == 0,
            },
        )

        self.logger.info(
            f"Pipeline [{self.source_doc_name}]: Pairwise complete | "
            f"comparisons={summary.total_comparisons} "
            f"winner={summary.winner_doc_id}"
        )

    async def _run_combine(self, result: SourceDocResult) -> None:
        """Run combine phase for THIS source doc."""
        if self._stop_requested():
            self.logger.info(f"Pipeline [{self.source_doc_name}]: Combine skipped - stop requested")
            return

        if not result.winner_doc_id:
            await self._record_degraded_phase(
                result,
                phase="combination",
                event_type="combine_skipped",
                description="Combine skipped: no winner was available to combine from.",
            )
            return

        if not self.config.combine_models:
            await self._record_degraded_phase(
                result,
                phase="combination",
                event_type="combine_skipped",
                description="Combine skipped: no combine models were configured.",
            )
            return

        try:
            combine_adapter = CombineAdapter(self._fpf_adapter)

            # Get top docs from pairwise results
            top_docs = []
            top_ids: list[str] = []
            if result.pairwise_results and result.pairwise_results.rankings:
                top_ids = [doc_id for doc_id, rating in result.pairwise_results.rankings[:2]]
                top_docs = [
                    doc.content
                    for doc in result.generated_docs
                    if doc.doc_id in top_ids
                ]

            if len(top_docs) < 2:
                await self._record_degraded_phase(
                    result,
                    phase="combination",
                    event_type="combine_skipped",
                    description=(
                        "Combine skipped: fewer than 2 usable top documents survived. "
                        "Preserving the current winner as the final output."
                    ),
                    details={"top_doc_count": len(top_docs)},
                )
                return

            reusable_combined_docs = await self._load_reusable_combined_docs(
                expected_input_doc_ids=top_ids,
            )

            combine_instructions = self.config.combine_instructions
            original_instructions = self.content  # The source document content

            # Try each combine model
            for model_idx, combine_model in enumerate(self.config.combine_models):
                if self._stop_requested():
                    self.logger.info(
                        f"Pipeline [{self.source_doc_name}]: Stop requested during combine; "
                        "skipping remaining combine models"
                    )
                    break

                cached_combined_doc = reusable_combined_docs.get(combine_model)
                if cached_combined_doc is not None:
                    result.combined_doc = cached_combined_doc
                    result.combined_docs.append(cached_combined_doc)
                    self.logger.info(
                        "Pipeline [%s]: Reused combined output for %s from durable checkpoints",
                        self.source_doc_name,
                        combine_model,
                    )
                    continue

                if ":" not in combine_model:
                    self.logger.error(f"Pipeline [{self.source_doc_name}]: Invalid combine model format: {combine_model}")
                    continue

                provider, model_name = combine_model.split(":", 1)

                safe_model_name = combine_model.replace(":", "_").replace("/", "_")
                combine_task_id = f"{self.source_doc_id[-8:]}.combine.{model_idx}.{safe_model_name}"

                combine_gen_config = GenerationConfig(
                    provider=provider,
                    model=model_name,
                    extra=compile_combine_adapter_extra(
                        combine_config=self.config.combine_config,
                        task_id=combine_task_id,
                        run_id=self.run_id,
                        document_id=self.source_doc_id,
                        max_tokens=self.config.combine_max_tokens,
                        key_mode=self.config.key_mode,
                        request_timeout=self.config.request_timeout,
                        fpf_max_retries=self.config.fpf_max_retries,
                        fpf_retry_delay=self.config.fpf_retry_delay,
                    ),
                )

                combine_started_at = datetime.utcnow()
                try:
                    self.logger.info(f"Pipeline [{self.source_doc_name}]: Combining with {combine_model}")

                    combine_result = await combine_adapter.combine(
                        reports=top_docs,
                        instructions=combine_instructions,
                        config=combine_gen_config,
                        user_uuid=self.config.user_uuid,
                        original_instructions=original_instructions,
                    )
                    combine_completed_at = datetime.utcnow()
                    combine_duration = (combine_completed_at - combine_started_at).total_seconds()

                    # Create unique doc_id
                    short_source_id = self.source_doc_id[-8:] if len(self.source_doc_id) >= 8 else self.source_doc_id
                    file_uuid = str(uuid4())[:4]
                    combined_doc_id = f"combined.{short_source_id}.{file_uuid}.{safe_model_name}"

                    # Create GeneratedDocument for combined content
                    combined_doc = GeneratedDocument(
                        doc_id=combined_doc_id,
                        content=combine_result.content,
                        generator=GeneratorType.FPF,
                        model=combine_model,
                        source_doc_id=self.source_doc_id,
                        iteration=1,
                                duration_seconds=combine_duration,
                        started_at=combine_started_at,
                        completed_at=combine_completed_at,
                    )

                    result.combined_doc = combined_doc
                    result.combined_docs.append(combined_doc)  # Add to list of all combined docs

                    # Save combined content to file
                    _combined_file_path = await self._save_generated_content(combined_doc)

                    try:
                        await write_combined_doc(
                            self.run_id,
                            self.config.user_uuid,
                            generated_doc=combined_doc,
                            input_doc_ids=top_ids,
                            combine_strategy=self.config.combine_strategy,
                            file_path=_combined_file_path,
                        )
                        await mark_phase_checkpoint_completed(
                            self.run_id,
                            self.config.user_uuid,
                            source_doc_id=self.source_doc_id,
                            phase="combine",
                            model_name=combine_model,
                            iteration=model_idx + 1,
                            output_ref=self._combine_output_ref(combined_doc.doc_id),
                            generator="combine",
                        )
                    except Exception as exc:
                        self.logger.warning(
                            "Pipeline [%s]: combine checkpoint persistence failed (non-fatal): %s",
                            self.source_doc_name,
                            exc,
                        )

                    # Emit combine timeline event
                    await self._emit_timeline_event(
                        phase="combination",
                        event_type="combine",
                        description=f"Combined documents using {combine_model}",
                        model=combine_model,
                        timestamp=combine_started_at,
                        completed_at=combine_completed_at,
                        duration_seconds=combine_duration,
                        success=True,
                        details={"combined_doc_id": combined_doc_id},
                    )

                    self.logger.info(f"Pipeline [{self.source_doc_name}]: Combine with {combine_model} succeeded")
                    if self._stop_requested():
                        self.logger.info(
                            f"Pipeline [{self.source_doc_name}]: Stop requested after combine with "
                            f"{combine_model}; skipping remaining combine models"
                        )
                        break
                    # Continue to next model - don't break, process all combine models

                except Exception as e:
                    self.logger.error(f"Pipeline [{self.source_doc_name}]: Combine with {combine_model} failed: {e}")
                    result.errors.append(f"Combine with {combine_model} failed: {str(e)}")

            if not result.combined_docs:
                await self._record_degraded_phase(
                    result,
                    phase="combination",
                    event_type="combine_failed",
                    description=(
                        f"Combine failed: all {len(self.config.combine_models)} combine models failed. "
                        "Preserving the current winner as the final output."
                    ),
                )

        except Exception as e:
            self.logger.error(f"Pipeline [{self.source_doc_name}]: Combine failed: {e}")
            await self._record_degraded_phase(
                result,
                phase="combination",
                event_type="combine_failed",
                description=f"Combine failed: {str(e)}",
            )

    async def _run_post_combine_eval(self, result: SourceDocResult) -> None:
        """Run post-combine pairwise evaluation for THIS source doc."""
        if self._stop_requested():
            self.logger.info(f"Pipeline [{self.source_doc_name}]: Post-combine eval skipped - stop requested")
            return

        if not result.combined_docs:
            await self._record_degraded_phase(
                result,
                phase="post_combine_pairwise",
                event_type="post_combine_skipped",
                description="Post-combine pairwise skipped: no combined document was available.",
            )
            return

        if not result.pairwise_results or not result.pairwise_results.rankings:
            await self._record_degraded_phase(
                result,
                phase="post_combine_pairwise",
                event_type="post_combine_skipped",
                description="Post-combine pairwise skipped: no pre-combine pairwise rankings were available.",
            )
            return

        try:
            original_limit = self.config.post_combine_top_n or 2
            evaluator = self._build_pairwise_evaluator(
                top_n=original_limit,
                phase_label="post-combine",
            )

            # Collect documents for comparison
            all_doc_ids = []
            all_contents = {}

            # Compare combined outputs against the configured top-ranked originals.
            ranking_scores = {
                doc_id: rating for doc_id, rating in result.pairwise_results.rankings
            }
            ranked_original_ids = [doc_id for doc_id, _rating in result.pairwise_results.rankings]
            original_doc_ids = evaluator.filter_top_n(ranked_original_ids, ranking_scores)

            for doc in result.generated_docs:
                if doc.doc_id in original_doc_ids:
                    all_doc_ids.append(doc.doc_id)
                    all_contents[doc.doc_id] = doc.content

            # Add all combined docs to comparison
            for combined_doc in result.combined_docs:
                all_doc_ids.append(combined_doc.doc_id)
                all_contents[combined_doc.doc_id] = combined_doc.content

            if len(all_doc_ids) < 2:
                await self._record_degraded_phase(
                    result,
                    phase="post_combine_pairwise",
                    event_type="post_combine_skipped",
                    description=(
                        "Post-combine pairwise skipped: not enough documents remained after filtering."
                    ),
                    details={"doc_count": len(all_doc_ids)},
                )
                return

            cached_summary = await self._load_reusable_pairwise_summary(
                comparison_type="post_combine",
                expected_doc_ids=all_doc_ids,
            )
            if cached_summary is not None:
                result.post_combine_eval_results = cached_summary
                self.logger.info(
                    "Pipeline [%s]: Reused completed post-combine pairwise phase from durable checkpoints",
                    self.source_doc_name,
                )
                return

            await self._clear_nonreusable_pairwise_state(comparison_type="post_combine")

            # Run pairwise
            post_combine_start = datetime.utcnow()
            expected_comparisons = (
                len(list(itertools.combinations(all_doc_ids, 2)))
                * self.config.eval_iterations
                * len(self.config.eval_judge_models or [])
            )
            summary = await evaluator.evaluate_all_pairs(all_doc_ids, all_contents)
            post_combine_end = datetime.utcnow()
            post_combine_duration = (post_combine_end - post_combine_start).total_seconds()
            self._apply_pairwise_deviations(summary, phase_label="post-combine")
            degraded = summary.total_comparisons < expected_comparisons
            if summary.results:
                try:
                    await write_pairwise_results(
                        self.run_id,
                        self.config.user_uuid,
                        source_doc_id=self.source_doc_id,
                        summary=summary,
                        comparison_type="post_combine",
                    )
                    if not degraded:
                        await mark_phase_checkpoint_completed(
                            self.run_id,
                            self.config.user_uuid,
                            source_doc_id=self.source_doc_id,
                            phase="pairwise",
                            model_name=self._pairwise_checkpoint_model_name("post_combine"),
                            iteration=1,
                            output_ref=self._pairwise_checkpoint_output_ref("post_combine"),
                        )
                    else:
                        await self._clear_nonreusable_pairwise_state(
                            comparison_type="post_combine",
                        )
                except Exception as exc:
                    self.logger.warning(
                        "Pipeline [%s]: post-combine checkpoint persistence failed (non-fatal): %s",
                        self.source_doc_name,
                        exc,
                    )
            if summary.total_comparisons == 0:
                await self._record_degraded_phase(
                    result,
                    phase="post_combine_pairwise",
                    event_type="post_combine_failed",
                    description=(
                        "Post-combine pairwise produced no successful comparisons after retries."
                    ),
                    details={
                        "expected_comparisons": expected_comparisons,
                        "successful_comparisons": summary.total_comparisons,
                    },
                )
            elif degraded:
                await self._record_degraded_phase(
                    result,
                    phase="post_combine_pairwise",
                    event_type="post_combine_degraded",
                    description=(
                        f"Post-combine pairwise completed partially: {summary.total_comparisons} of "
                        f"{expected_comparisons} expected comparisons succeeded after retries."
                    ),
                    details={
                        "expected_comparisons": expected_comparisons,
                        "successful_comparisons": summary.total_comparisons,
                    },
                )

            result.post_combine_eval_results = summary

            # Emit timeline event
            await self._emit_timeline_event(
                phase="post_combine_pairwise",
                event_type="pairwise_eval",
                description=f"Post-combine pairwise: {summary.total_comparisons} comparisons",
                model=", ".join(self.config.eval_judge_models),
                timestamp=post_combine_start,
                completed_at=post_combine_end,
                duration_seconds=post_combine_duration,
                success=not degraded and summary.total_comparisons > 0,
                details={
                    "total_comparisons": summary.total_comparisons,
                    "expected_comparisons": expected_comparisons,
                    "winner_doc_id": summary.winner_doc_id,
                    "degraded": degraded or summary.total_comparisons == 0,
                },
            )

            self.logger.info(
                f"Pipeline [{self.source_doc_name}]: Post-combine eval complete | "
                f"winner={summary.winner_doc_id}"
            )

        except Exception as e:
            self.logger.error(f"Pipeline [{self.source_doc_name}]: Post-combine eval failed: {e}")
            await self._record_degraded_phase(
                result,
                phase="post_combine_pairwise",
                event_type="post_combine_failed",
                description=f"Post-combine eval failed: {str(e)}",
            )
