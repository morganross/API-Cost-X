"""
Evaluation orchestration service.

Coordinates single-doc and pairwise evaluation phases,
manages evaluation runs, and produces final rankings.
"""

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .criteria import CriteriaManager
from .elo import EloCalculator
from .models import (
    DocumentRanking,
    EloRating,
    EvaluationRun,
    EvaluationSummary,
    EvaluationType,
)
from .pairwise import PairwiseConfig, PairwiseEvaluator, PairwiseSummary
from .single_doc import (
    DocumentInput,
    SingleDocEvaluator,
    SingleEvalConfig,
    SingleEvalSummary,
)

logger = logging.getLogger(__name__)


@dataclass
class EvaluationConfig:
    """
    Complete configuration for evaluation pipeline.

    Attributes:
        iterations: Number of evaluation iterations
        judge_models: List of judge models to use
        criteria_path: Path to custom criteria YAML
        enable_single_eval: Run single-doc evaluation phase
        enable_pairwise: Run pairwise comparison phase
        pairwise_top_n: Only compare top N from single eval
        k_factor: Elo K-factor
        concurrent_limit: Max concurrent evaluations
    """

    iterations: int = 1
    judge_models: List[str] = field(default_factory=list)  # REQUIRED - must be set by preset
    criteria_path: Optional[str] = None

    # Phase toggles
    enable_single_eval: bool = True
    enable_pairwise: bool = True

    # Pairwise settings
    pairwise_top_n: Optional[int] = None
    k_factor: float = 32.0

    # Performance
    concurrent_limit: int = 3
    temperature: float = 0.0
    max_tokens: int = 4096

    def to_single_config(self) -> SingleEvalConfig:
        """Create SingleEvalConfig from this config."""
        return SingleEvalConfig(
            iterations=self.iterations,
            judge_models=self.judge_models,
            criteria_path=self.criteria_path,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            concurrent_limit=self.concurrent_limit,
        )

    def to_pairwise_config(self) -> PairwiseConfig:
        """Create PairwiseConfig from this config."""
        return PairwiseConfig(
            iterations=self.iterations,
            judge_models=self.judge_models,
            criteria_path=self.criteria_path,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            concurrent_limit=self.concurrent_limit,
            top_n=self.pairwise_top_n,
            k_factor=self.k_factor,
        )


@dataclass
class EvaluationInput:
    """Input for evaluation service."""

    documents: List[DocumentInput]
    run_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def doc_ids(self) -> List[str]:
        """Get list of document IDs."""
        return [d.doc_id for d in self.documents]

    @property
    def contents(self) -> Dict[str, str]:
        """Get dict mapping doc_id to content."""
        return {d.doc_id: d.content for d in self.documents}


@dataclass
class EvaluationResult:
    """Complete result of evaluation pipeline."""

    run_id: str
    started_at: datetime
    completed_at: datetime

    # Phase results
    single_eval_summaries: Optional[Dict[str, SingleEvalSummary]] = None
    pairwise_summary: Optional[PairwiseSummary] = None

    # Final rankings
    rankings: List[DocumentRanking] = field(default_factory=list)
    winner_doc_id: Optional[str] = None

    # Stats
    total_evaluations: int = 0

    @property
    def duration_seconds(self) -> float:
        """Total evaluation duration in seconds."""
        return (self.completed_at - self.started_at).total_seconds()

    def to_summary(self) -> EvaluationSummary:
        """Convert to EvaluationSummary."""
        return EvaluationSummary(
            run_id=self.run_id,
            rankings=self.rankings,
            winner_doc_id=self.winner_doc_id or "",
            total_evaluations=self.total_evaluations,
            duration_seconds=self.duration_seconds,
        )


ProgressCallback = Callable[[str, str, int, int], None]  # (phase, status, completed, total)


class EvaluationService:
    """
    Main evaluation orchestration service.

    Coordinates single-doc and pairwise evaluation phases
    to produce final document rankings.
    """

    def __init__(
        self,
        config: Optional[EvaluationConfig] = None,
        criteria_manager: Optional[CriteriaManager] = None,
        user_uuid: Optional[str] = None,
    ):
        """
        Initialize the service.

        Args:
            config: Evaluation configuration
            criteria_manager: Criteria manager instance
        """
        self.config = config or EvaluationConfig()
        self.criteria = criteria_manager or CriteriaManager(self.config.criteria_path)
        self.user_uuid = user_uuid

        # Initialize evaluators
        self._single_eval = SingleDocEvaluator(
            config=self.config.to_single_config(),
            criteria_manager=self.criteria,
            user_uuid=self.user_uuid,
        )
        self._pairwise_eval = PairwiseEvaluator(
            config=self.config.to_pairwise_config(),
            criteria_manager=self.criteria,
            user_uuid=self.user_uuid,
        )

    async def evaluate(
        self,
        input_data: EvaluationInput,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> EvaluationResult:
        """
        Run complete evaluation pipeline.

        Pipeline:
        1. Single-doc evaluation (if enabled) - grades each doc
        2. Pairwise comparison (if enabled) - head-to-head battles
        3. Compute final rankings from Elo + single scores

        Args:
            input_data: Documents to evaluate
            progress_callback: Optional progress callback

        Returns:
            Complete evaluation result with rankings
        """
        run_id = input_data.run_id or str(uuid.uuid4())[:8]
        started_at = datetime.utcnow()

        logger.info(
            f"Starting evaluation run {run_id} | "
            f"docs={len(input_data.documents)} "
            f"single={self.config.enable_single_eval} "
            f"pairwise={self.config.enable_pairwise}"
        )

        single_summaries: Optional[Dict[str, SingleEvalSummary]] = None
        pairwise_summary: Optional[PairwiseSummary] = None
        total_evals = 0

        # Phase 1: Single-doc evaluation
        if self.config.enable_single_eval:
            if progress_callback:
                progress_callback("single", "starting", 0, len(input_data.documents))

            single_summaries = await self._single_eval.evaluate_documents(
                input_data.documents
            )

            total_evals += sum(s.num_evaluations for s in single_summaries.values())

            logger.info(
                f"Single eval complete | "
                f"docs={len(single_summaries)} evals={total_evals}"
            )

            if progress_callback:
                progress_callback("single", "complete", len(single_summaries), len(input_data.documents))

        # Phase 2: Pairwise comparison
        if self.config.enable_pairwise:
            doc_ids = input_data.doc_ids
            contents = input_data.contents

            # Filter to top N if single eval was done
            if single_summaries and self.config.pairwise_top_n:
                scores = {
                    doc_id: summary.avg_score
                    for doc_id, summary in single_summaries.items()
                }
                doc_ids = self._pairwise_eval.filter_top_n(
                    doc_ids,
                    scores,
                    self.config.pairwise_top_n,
                )
                contents = {d: contents[d] for d in doc_ids}

                logger.info(
                    f"Filtered to top {len(doc_ids)} docs for pairwise"
                )

            if len(doc_ids) >= 2:
                if progress_callback:
                    num_pairs = len(doc_ids) * (len(doc_ids) - 1) // 2
                    progress_callback("pairwise", "starting", 0, num_pairs)

                pairwise_summary = await self._pairwise_eval.evaluate_all_pairs(
                    doc_ids,
                    contents,
                )

                total_evals += pairwise_summary.total_comparisons

                logger.info(
                    f"Pairwise complete | "
                    f"comparisons={pairwise_summary.total_comparisons} "
                    f"winner={pairwise_summary.winner_doc_id}"
                )

                if progress_callback:
                    progress_callback("pairwise", "complete", pairwise_summary.total_pairs, pairwise_summary.total_pairs)

        # Compute final rankings
        rankings = self._compute_rankings(
            input_data.doc_ids,
            single_summaries,
            pairwise_summary,
        )

        winner_doc_id = rankings[0].doc_id if rankings else None
        completed_at = datetime.utcnow()

        result = EvaluationResult(
            run_id=run_id,
            started_at=started_at,
            completed_at=completed_at,
            single_eval_summaries=single_summaries,
            pairwise_summary=pairwise_summary,
            rankings=rankings,
            winner_doc_id=winner_doc_id,
            total_evaluations=total_evals,
        )

        logger.info(
            f"Evaluation complete | "
            f"run={run_id} winner={winner_doc_id} "
            f"duration={result.duration_seconds:.1f}s"
        )

        return result

    def _compute_rankings(
        self,
        doc_ids: List[str],
        single_summaries: Optional[Dict[str, SingleEvalSummary]],
        pairwise_summary: Optional[PairwiseSummary],
    ) -> List[DocumentRanking]:
        """
        Compute final rankings from evaluation results.

        Priority:
        1. Elo rating from pairwise (if available)
        2. Average score from single eval
        """
        rankings: List[DocumentRanking] = []

        # Build Elo lookup
        elo_lookup: Dict[str, EloRating] = {}
        if pairwise_summary:
            for rating in pairwise_summary.elo_ratings:
                elo_lookup[rating.doc_id] = rating

        # Build score lookup
        score_lookup: Dict[str, float] = {}
        if single_summaries:
            for doc_id, summary in single_summaries.items():
                score_lookup[doc_id] = summary.avg_score

        # Create rankings for all docs
        for doc_id in doc_ids:
            elo = elo_lookup.get(doc_id)
            avg_score = score_lookup.get(doc_id)

            rankings.append(DocumentRanking(
                doc_id=doc_id,
                rank=0,  # Will be set after sorting
                elo_rating=elo.rating if elo else None,
                avg_score=avg_score,
                wins=elo.wins if elo else 0,
                losses=elo.losses if elo else 0,
            ))

        # Sort by Elo (primary) or avg_score (fallback)
        def sort_key(r: DocumentRanking) -> tuple:
            # Higher is better for both metrics
            elo = r.elo_rating if r.elo_rating is not None else -1
            score = r.avg_score if r.avg_score is not None else -1
            return (elo, score)

        rankings.sort(key=sort_key, reverse=True)

        # Assign ranks
        for i, ranking in enumerate(rankings, 1):
            ranking.rank = i

        return rankings

    async def evaluate_single_only(
        self,
        input_data: EvaluationInput,
    ) -> Dict[str, SingleEvalSummary]:
        """
        Run only single-doc evaluation.

        Args:
            input_data: Documents to evaluate

        Returns:
            Dict mapping doc_id to summary
        """
        return await self._single_eval.evaluate_documents(input_data.documents)

    async def evaluate_pairwise_only(
        self,
        input_data: EvaluationInput,
        scores: Optional[Dict[str, float]] = None,
    ) -> PairwiseSummary:
        """
        Run only pairwise evaluation.

        Args:
            input_data: Documents to evaluate
            scores: Optional scores for top-N filtering

        Returns:
            Pairwise evaluation summary
        """
        doc_ids = input_data.doc_ids
        contents = input_data.contents

        if scores and self.config.pairwise_top_n:
            doc_ids = self._pairwise_eval.filter_top_n(
                doc_ids,
                scores,
                self.config.pairwise_top_n,
            )
            contents = {d: contents[d] for d in doc_ids}

        return await self._pairwise_eval.evaluate_all_pairs(doc_ids, contents)
