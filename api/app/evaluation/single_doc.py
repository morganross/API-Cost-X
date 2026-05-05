"""
Single-document graded evaluation service.

Evaluates documents against criteria and stores results.
"""

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .criteria import CriteriaManager, parse_criteria_yaml
from .judge import Judge, JudgeConfig, FpfStatsTracker
from .models import CriterionScore, SingleEvalResult

logger = logging.getLogger(__name__)

# Callback fired after each individual judge evaluation completes
# Args: (doc_id, model, trial, result)
EvalCompleteCallback = Callable[[str, str, int, SingleEvalResult], Awaitable[None]]


@dataclass
class SingleEvalConfig:
    """Configuration for single-document evaluation."""

    iterations: int = 1
    judge_models: List[str] = field(default_factory=list)  # REQUIRED - must be set by preset
    criteria_path: Optional[str] = None
    temperature: float = 0.0
    max_tokens: int = 16384
    thinking_budget_tokens: Optional[int] = None  # Thinking/reasoning budget (None = provider default)
    concurrent_limit: int = 3  # Max concurrent evaluations
    timeout_seconds: int = 600  # Per-call timeout (GUI EvalPanel)
    retries: int = 0
    # NOTE: enable_grounding removed - FPF always uses grounding, non-configurable

    # Custom instructions from Content Library
    custom_instructions: Optional[str] = None
    custom_criteria: Optional[str] = None
    key_mode: str = 'byok'  # 'byok' = user keys, 'system' = platform keys
    trial_numbers_by_judge: Dict[str, List[int]] = field(default_factory=dict)

    def trial_numbers_for_model(self, model: str) -> List[int]:
        explicit_trials = self.trial_numbers_by_judge.get(model)
        if explicit_trials:
            normalized = sorted({int(trial) for trial in explicit_trials if int(trial) > 0})
            if normalized:
                return normalized
        return list(range(1, self.iterations + 1))

    def to_judge_config(self, model: str) -> JudgeConfig:
        """Create JudgeConfig for a specific model."""
        return JudgeConfig(
            model=model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            thinking_budget_tokens=self.thinking_budget_tokens,
            timeout_seconds=self.timeout_seconds,
            retries=self.retries,
            key_mode=self.key_mode,
        )


@dataclass
class DocumentInput:
    """Input for single-document evaluation."""

    doc_id: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SingleEvalSummary:
    """Summary of single-document evaluation results."""

    doc_id: str
    avg_score: float
    scores_by_criterion: Dict[str, float]
    num_evaluations: int
    results: List[SingleEvalResult]
    expected_evaluations: int = 0
    expected_judge_models: List[str] = field(default_factory=list)
    successful_judge_models: List[str] = field(default_factory=list)
    missing_judge_models: List[str] = field(default_factory=list)
    missing_evaluations_by_judge: Dict[str, int] = field(default_factory=dict)
    failed_evaluations: int = 0
    deviations_by_judge_criterion: Optional[Dict[str, Dict[str, float]]] = None  # { judge_model: { criterion: deviation } }

    @classmethod
    def from_results(
        cls,
        doc_id: str,
        results: List[SingleEvalResult],
        *,
        expected_judge_models: Optional[List[str]] = None,
        iterations: int = 1,
        expected_trials_by_judge: Optional[Dict[str, List[int]]] = None,
    ) -> "SingleEvalSummary":
        """
        Create summary from list of evaluation results.

        Args:
            doc_id: Document identifier
            results: List of evaluation results

        Returns:
            Summary with aggregated statistics
        """
        expected_judge_models = list(dict.fromkeys(expected_judge_models or []))
        expected_counts_by_judge: Dict[str, int] = {}
        for judge_model in expected_judge_models:
            explicit_trials = (expected_trials_by_judge or {}).get(judge_model)
            if explicit_trials:
                normalized_trials = {int(trial) for trial in explicit_trials if int(trial) > 0}
                expected_counts_by_judge[judge_model] = len(normalized_trials)
            else:
                expected_counts_by_judge[judge_model] = max(iterations, 0)
        expected_total = sum(expected_counts_by_judge.values())
        result_counts_by_judge = Counter(result.model for result in results)
        successful_judge_models = sorted(
            judge_model
            for judge_model, count in result_counts_by_judge.items()
            if count > 0
        )
        missing_evaluations_by_judge = {
            judge_model: max(expected_counts_by_judge.get(judge_model, 0) - result_counts_by_judge.get(judge_model, 0), 0)
            for judge_model in expected_judge_models
            if max(expected_counts_by_judge.get(judge_model, 0) - result_counts_by_judge.get(judge_model, 0), 0) > 0
        }
        missing_judge_models = sorted(
            judge_model
            for judge_model, missing_count in missing_evaluations_by_judge.items()
            if missing_count >= max(expected_counts_by_judge.get(judge_model, 0), 1)
        )
        failed_evaluations = max(expected_total - len(results), 0)

        if not results:
            return cls(
                doc_id=doc_id,
                avg_score=0.0,
                scores_by_criterion={},
                num_evaluations=0,
                results=[],
                expected_evaluations=expected_total,
                expected_judge_models=expected_judge_models,
                successful_judge_models=successful_judge_models,
                missing_judge_models=missing_judge_models,
                missing_evaluations_by_judge=missing_evaluations_by_judge,
                failed_evaluations=failed_evaluations,
            )

        # Aggregate scores by criterion
        criterion_scores: Dict[str, List[int]] = {}
        for result in results:
            for score in result.scores:
                if score.criterion not in criterion_scores:
                    criterion_scores[score.criterion] = []
                criterion_scores[score.criterion].append(score.score)

        # Calculate averages per criterion
        scores_by_criterion = {
            crit: sum(scores) / len(scores)
            for crit, scores in criterion_scores.items()
        }

        # Calculate overall average
        all_scores = [s for scores in criterion_scores.values() for s in scores]
        avg_score = sum(all_scores) / len(all_scores) if all_scores else 0.0

        return cls(
            doc_id=doc_id,
            avg_score=avg_score,
            scores_by_criterion=scores_by_criterion,
            num_evaluations=len(results),
            results=results,
            expected_evaluations=expected_total,
            expected_judge_models=expected_judge_models,
            successful_judge_models=successful_judge_models,
            missing_judge_models=missing_judge_models,
            missing_evaluations_by_judge=missing_evaluations_by_judge,
            failed_evaluations=failed_evaluations,
        )

    @staticmethod
    def calculate_deviations(
        summaries: Dict[str, "SingleEvalSummary"],
    ) -> Dict[str, Dict[str, float]]:
        """
        Calculate judge deviations from document averages across all documents.

        For each judge and criterion, calculates:
        - Per-document deviation: judge_score - document_mean_score
        - Returns: average of these deviations across all documents (as float)

        Args:
            summaries: Dict mapping doc_id to SingleEvalSummary

        Returns:
            Dict[judge_model, Dict[criterion, avg_deviation_as_float]]
        """
        if not summaries:
            return {}

        # Collect deviations: { judge_model: { criterion: [deviations] } }
        judge_criterion_deviations: Dict[str, Dict[str, List[float]]] = {}

        for doc_id, summary in summaries.items():
            # Calculate mean score per criterion for this document
            criterion_means: Dict[str, float] = {}
            criterion_counts: Dict[str, int] = {}

            for result in summary.results:
                for score in result.scores:
                    if score.criterion not in criterion_means:
                        criterion_means[score.criterion] = 0.0
                        criterion_counts[score.criterion] = 0
                    criterion_means[score.criterion] += score.score
                    criterion_counts[score.criterion] += 1

            # Calculate means
            for criterion in criterion_means:
                if criterion_counts[criterion] > 0:
                    criterion_means[criterion] /= criterion_counts[criterion]

            # Calculate deviations for each judge × criterion
            for result in summary.results:
                judge_model = result.model
                if judge_model not in judge_criterion_deviations:
                    judge_criterion_deviations[judge_model] = {}

                for score in result.scores:
                    criterion = score.criterion
                    if criterion in criterion_means:
                        deviation = score.score - criterion_means[criterion]

                        if criterion not in judge_criterion_deviations[judge_model]:
                            judge_criterion_deviations[judge_model][criterion] = []
                        judge_criterion_deviations[judge_model][criterion].append(deviation)

        # Calculate average deviations as floats
        result: Dict[str, Dict[str, float]] = {}
        for judge_model, criterion_devs in judge_criterion_deviations.items():
            result[judge_model] = {}
            all_criterion_deviations = []

            for criterion, deviations in criterion_devs.items():
                if deviations:
                    avg_deviation = sum(deviations) / len(deviations)
                    # Keep decimal precision
                    result[judge_model][criterion] = avg_deviation
                    all_criterion_deviations.append(avg_deviation)

            # Calculate total deviation across all criteria for this judge
            if all_criterion_deviations:
                total_deviation = sum(all_criterion_deviations) / len(all_criterion_deviations)
                result[judge_model]["__TOTAL__"] = total_deviation

        return result


ProgressCallback = Callable[[str, int, int], None]  # (doc_id, completed, total)


class SingleDocEvaluator:
    """
    Service for single-document graded evaluation.

    Evaluates documents against criteria using multiple iterations
    and judge models, then aggregates results.
    """

    def __init__(
        self,
        config: Optional[SingleEvalConfig] = None,
        criteria_manager: Optional[CriteriaManager] = None,
        stats_tracker: Optional[FpfStatsTracker] = None,
        user_uuid: Optional[str] = None,
        run_id: Optional[str] = None,
    ):
        """
        Initialize the evaluator.

        Args:
            config: Evaluation configuration
            criteria_manager: Criteria manager instance
            stats_tracker: Optional FPF stats tracker for live monitoring
            run_id: Run UUID for log correlation
        """
        self.config = config or SingleEvalConfig()
        self.criteria = criteria_manager or CriteriaManager(self.config.criteria_path)
        self.stats = stats_tracker
        self.user_uuid = user_uuid
        self.run_id = run_id
        self._judges: Dict[str, Judge] = {}

        # Parse custom_criteria YAML string from Content Library if provided
        if self.config.custom_criteria:
            parsed_criteria = parse_criteria_yaml(self.config.custom_criteria)
            if parsed_criteria:
                self.criteria.set_criteria(parsed_criteria)
                logger.info(f"Loaded {len(parsed_criteria)} criteria from Content Library custom_criteria")

    def _get_judge(self, model: str) -> Judge:
        """Get or create judge for a model."""
        if model not in self._judges:
            judge_config = self.config.to_judge_config(model)
            self._judges[model] = Judge(
                config=judge_config,
                criteria_manager=self.criteria,
                custom_prompt=self.config.custom_instructions,
                stats_tracker=self.stats,
                user_uuid=self.user_uuid,
                run_id=self.run_id,
            )
        return self._judges[model]

    async def evaluate_document(
        self,
        doc: DocumentInput,
        progress_callback: Optional[ProgressCallback] = None,
        on_eval_complete: Optional[EvalCompleteCallback] = None,
    ) -> SingleEvalSummary:
        """
        Evaluate a single document.

        Runs all iterations across all judge models and aggregates results.

        Args:
            doc: Document to evaluate
            progress_callback: Optional callback for progress updates
            on_eval_complete: Optional async callback fired after each judge evaluation

        Returns:
            Summary of evaluation results
        """
        results: List[SingleEvalResult] = []
        total_evals = sum(
            len(self.config.trial_numbers_for_model(model))
            for model in self.config.judge_models
        )
        completed = 0

        for model in self.config.judge_models:
            judge = self._get_judge(model)

            for trial in self.config.trial_numbers_for_model(model):
                eval_result = None
                try:
                    eval_result = await judge.evaluate_single(
                        doc_id=doc.doc_id,
                        content=doc.content,
                        trial=trial,
                    )
                    results.append(eval_result)
                    logger.info(
                        f"Single eval completed: {doc.doc_id} | "
                        f"model={model} trial={trial} avg={eval_result.average_score:.2f}"
                    )
                except Exception as e:
                    logger.error(
                        f"Single eval failed: {doc.doc_id} | "
                        f"model={model} trial={trial}: {e}"
                    )

                # Fire per-judge callback OUTSIDE the judge try/except so that a DB
                # write failure propagates to the caller instead of being swallowed.
                # If the judge itself failed, eval_result is None and we skip the callback.
                if eval_result is not None and on_eval_complete:
                    await on_eval_complete(doc.doc_id, model, trial, eval_result)

                completed += 1
                if progress_callback:
                    try:
                        progress_callback(doc.doc_id, completed, total_evals)
                    except Exception:
                        pass

        return SingleEvalSummary.from_results(
            doc_id=doc.doc_id,
            results=results,
            expected_judge_models=self.config.judge_models,
            iterations=self.config.iterations,
            expected_trials_by_judge=self.config.trial_numbers_by_judge or None,
        )

    async def evaluate_documents(
        self,
        docs: List[DocumentInput],
        progress_callback: Optional[Callable[[str, int, int, int, int], None]] = None,
        on_eval_complete: Optional[EvalCompleteCallback] = None,
    ) -> Dict[str, SingleEvalSummary]:
        """
        Evaluate multiple documents with concurrency control.

        Args:
            docs: List of documents to evaluate
            progress_callback: Callback(doc_id, doc_completed, total_docs, eval_completed, total_evals)
            on_eval_complete: Optional async callback fired after each individual judge evaluation

        Returns:
            Dict mapping doc_id to evaluation summary
        """
        results: Dict[str, SingleEvalSummary] = {}
        semaphore = asyncio.Semaphore(self.config.concurrent_limit)
        total_docs = len(docs)
        completed_docs = 0

        async def eval_with_limit(doc: DocumentInput) -> tuple[str, SingleEvalSummary]:
            nonlocal completed_docs
            async with semaphore:
                summary = await self.evaluate_document(doc, on_eval_complete=on_eval_complete)
                completed_docs += 1
                if progress_callback:
                    try:
                        progress_callback(
                            doc.doc_id,
                            completed_docs,
                            total_docs,
                            summary.num_evaluations,
                            self.config.iterations * len(self.config.judge_models),
                        )
                    except Exception:
                        pass
                return doc.doc_id, summary

        # Run all evaluations concurrently (with limit)
        tasks = [eval_with_limit(doc) for doc in docs]
        for coro in asyncio.as_completed(tasks):
            doc_id, summary = await coro
            results[doc_id] = summary

        return results

    def rank_documents(
        self,
        summaries: Dict[str, SingleEvalSummary],
    ) -> List[tuple[str, float]]:
        """
        Rank documents by their evaluation scores.

        Args:
            summaries: Dict mapping doc_id to summary

        Returns:
            List of (doc_id, score) tuples sorted by score descending
        """
        rankings = []
        for doc_id, summary in summaries.items():
            score = summary.avg_score
            rankings.append((doc_id, score))

        rankings.sort(key=lambda x: x[1], reverse=True)
        return rankings

    def get_top_n(
        self,
        summaries: Dict[str, SingleEvalSummary],
        n: int,
    ) -> List[str]:
        """
        Get top N document IDs by score.

        Args:
            summaries: Dict mapping doc_id to summary
            n: Number of top documents to return

        Returns:
            List of top N doc_ids
        """
        rankings = self.rank_documents(summaries)
        return [doc_id for doc_id, _ in rankings[:n]]
