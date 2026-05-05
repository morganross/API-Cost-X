"""
Pairwise comparison evaluation service.

Compares documents head-to-head to determine relative quality.
"""

import asyncio
import itertools
import logging
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .criteria import CriteriaManager
from .elo import EloCalculator, EloConfig
from .judge import Judge, JudgeConfig, FpfStatsTracker
from .models import EloRating, EvaluationCriterion, PairwiseResult

logger = logging.getLogger(__name__)


@dataclass
class PairwiseConfig:
    """Configuration for pairwise evaluation."""

    iterations: int = 1  # Iterations per pair
    judge_models: List[str] = field(default_factory=list)  # REQUIRED - must be set by preset
    criteria_path: Optional[str] = None
    temperature: float = 0.0
    max_tokens: int = 16384
    thinking_budget_tokens: Optional[int] = None  # Thinking/reasoning budget (None = provider default)
    retries: int = 3
    concurrent_limit: int = 3
    timeout_seconds: int = 600  # Per-call timeout (GUI EvalPanel)

    # Top-N filtering
    top_n: Optional[int] = None  # If set, only compare top N by single-doc score

    # Elo settings
    k_factor: float = 32.0
    initial_rating: float = 1000.0

    # Randomization
    randomize_order: bool = True  # Randomize A/B presentation order

    # Custom instructions from Content Library
    custom_instructions: Optional[str] = None
    key_mode: str = 'byok'  # 'byok' = user keys, 'system' = platform keys

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

    def to_elo_config(self) -> EloConfig:
        """Create EloConfig from pairwise settings."""
        return EloConfig(
            k_factor=self.k_factor,
            initial_rating=self.initial_rating,
        )


@dataclass
class DocumentPair:
    """A pair of documents for comparison."""

    doc_id_1: str
    content_1: str
    doc_id_2: str
    content_2: str

    def swap(self) -> "DocumentPair":
        """Return a new pair with documents swapped."""
        return DocumentPair(
            doc_id_1=self.doc_id_2,
            content_1=self.content_2,
            doc_id_2=self.doc_id_1,
            content_2=self.content_1,
        )


@dataclass
class PairwiseSummary:
    """Summary of pairwise evaluation results."""

    total_comparisons: int
    total_pairs: int
    results: List[PairwiseResult]
    elo_ratings: List[EloRating]
    winner_doc_id: Optional[str]
    deviations_by_judge: Optional[Dict[str, int]] = None  # { judge_model: deviation_int }

    @property
    def rankings(self) -> List[Tuple[str, float]]:
        """Get rankings as (doc_id, rating) tuples."""
        return [(r.doc_id, r.rating) for r in self.elo_ratings]

    @staticmethod
    def calculate_deviations(results: List[PairwiseResult]) -> Dict[str, int]:
        """
        Calculate consensus agreement deviation for each judge.

        For each comparison (A vs B), determine consensus winner (most votes).
        Track per-judge: did they pick the consensus winner?
        Calculate agreement rate: agreements / total_comparisons
        Compute deviation from mean agreement rate.

        Args:
            results: All pairwise results

        Returns:
            Dict mapping judge_model to deviation (signed integer percentage)
        """
        if not results:
            return {}

        # Group results by comparison pair
        comparisons: Dict[Tuple[str, str], List[PairwiseResult]] = {}
        for result in results:
            # Normalize pair order (sort doc IDs to treat A-vs-B same as B-vs-A)
            pair = tuple(sorted([result.doc_id_1, result.doc_id_2]))
            if pair not in comparisons:
                comparisons[pair] = []
            comparisons[pair].append(result)

        # For each comparison, determine consensus winner
        # and track which judges agreed
        judge_agreements: Dict[str, List[bool]] = {}

        for pair, pair_results in comparisons.items():
            # Count votes for each document
            votes: Dict[str, int] = {}
            for result in pair_results:
                winner = result.winner_doc_id
                votes[winner] = votes.get(winner, 0) + 1

            # Determine consensus winner (most votes)
            if not votes:
                continue
            consensus_winner = max(votes.items(), key=lambda x: x[1])[0]

            # Track agreement for each judge
            for result in pair_results:
                judge = result.model
                agreed = (result.winner_doc_id == consensus_winner)

                if judge not in judge_agreements:
                    judge_agreements[judge] = []
                judge_agreements[judge].append(agreed)

        # Calculate agreement rates
        agreement_rates: Dict[str, float] = {}
        for judge, agreements in judge_agreements.items():
            if agreements:
                agreement_rates[judge] = sum(agreements) / len(agreements) * 100.0

        if not agreement_rates:
            return {}

        # Calculate mean agreement rate
        mean_rate = sum(agreement_rates.values()) / len(agreement_rates)

        # Calculate deviations from mean
        deviations: Dict[str, int] = {}
        for judge, rate in agreement_rates.items():
            deviation = round(rate - mean_rate)
            deviations[judge] = deviation

        return deviations


PairwiseProgressCallback = Callable[[int, int, str, str], None]  # (completed, total, doc1, doc2)


class PairwiseEvaluator:
    """
    Service for pairwise document comparison.

    Compares documents head-to-head and maintains Elo ratings
    to determine overall ranking.
    """

    def __init__(
        self,
        config: Optional[PairwiseConfig] = None,
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
        self.config = config or PairwiseConfig()
        self.criteria = criteria_manager or CriteriaManager(self.config.criteria_path)
        self.stats = stats_tracker
        self.user_uuid = user_uuid
        self.run_id = run_id
        self._judges: Dict[str, Judge] = {}
        self._elo = EloCalculator(self.config.to_elo_config())

    def _get_judge(self, model: str) -> Judge:
        """Get or create judge for a model."""
        if model not in self._judges:
            judge_config = self.config.to_judge_config(model)
            logger.info(
                "Pairwise judge config: model=%s temperature=%s max_tokens=%s thinking_budget_tokens=%s retries=%s timeout_seconds=%s key_mode=%s",
                model,
                judge_config.temperature,
                judge_config.max_tokens,
                judge_config.thinking_budget_tokens,
                judge_config.retries,
                judge_config.timeout_seconds,
                judge_config.key_mode,
            )
            self._judges[model] = Judge(
                config=judge_config,
                criteria_manager=self.criteria,
                custom_prompt=self.config.custom_instructions,
                stats_tracker=self.stats,
                user_uuid=self.user_uuid,
                run_id=self.run_id,
            )
        return self._judges[model]

    def generate_pairs(
        self,
        doc_ids: List[str],
        contents: Dict[str, str],
    ) -> List[DocumentPair]:
        """
        Generate all unique pairs for comparison.

        Args:
            doc_ids: List of document IDs to compare
            contents: Dict mapping doc_id to content

        Returns:
            List of DocumentPair objects
        """
        pairs = []
        for id1, id2 in itertools.combinations(doc_ids, 2):
            pair = DocumentPair(
                doc_id_1=id1,
                content_1=contents[id1],
                doc_id_2=id2,
                content_2=contents[id2],
            )
            # Optionally randomize order to reduce position bias
            if self.config.randomize_order and random.random() < 0.5:
                pair = pair.swap()
            pairs.append(pair)

        return pairs

    async def compare_pair(
        self,
        pair: DocumentPair,
        trial: int = 1,
    ) -> List[PairwiseResult]:
        """
        Compare a single pair across all judge models.

        Args:
            pair: Document pair to compare
            trial: Trial number

        Returns:
            List of PairwiseResult (one per judge model)
        """
        results = []

        for model in self.config.judge_models:
            judge = self._get_judge(model)

            try:
                result = await judge.evaluate_pairwise(
                    doc_id_1=pair.doc_id_1,
                    content_1=pair.content_1,
                    doc_id_2=pair.doc_id_2,
                    content_2=pair.content_2,
                    trial=trial,
                )
                results.append(result)

                # Update Elo ratings immediately
                self._elo.process_result(result)

                logger.info(
                    f"Pairwise: {pair.doc_id_1} vs {pair.doc_id_2} | "
                    f"winner={result.winner_doc_id} model={model}"
                )

            except Exception as e:
                logger.error(
                    f"Pairwise failed: {pair.doc_id_1} vs {pair.doc_id_2} | "
                    f"model={model}: {e}"
                )

        return results

    async def evaluate_all_pairs(
        self,
        doc_ids: List[str],
        contents: Dict[str, str],
        progress_callback: Optional[PairwiseProgressCallback] = None,
    ) -> PairwiseSummary:
        """
        Run full pairwise evaluation across all document pairs.

        Args:
            doc_ids: List of document IDs to compare
            contents: Dict mapping doc_id to content
            progress_callback: Optional progress callback

        Returns:
            Summary with all results and final rankings
        """
        # Reset Elo calculator for fresh run
        self._elo.reset()

        # Generate pairs
        pairs = self.generate_pairs(doc_ids, contents)
        total_comparisons = len(pairs) * self.config.iterations * len(self.config.judge_models)
        logger.info(
            "Pairwise run start: doc_count=%s pair_count=%s iterations=%s judge_models=%s concurrent_limit=%s randomize_order=%s top_n=%s key_mode=%s timeout_seconds=%s max_tokens=%s thinking_budget_tokens=%s retries=%s",
            len(doc_ids),
            len(pairs),
            self.config.iterations,
            list(self.config.judge_models),
            self.config.concurrent_limit,
            self.config.randomize_order,
            self.config.top_n,
            self.config.key_mode,
            self.config.timeout_seconds,
            self.config.max_tokens,
            self.config.thinking_budget_tokens,
            self.config.retries,
        )

        all_results: List[PairwiseResult] = []
        completed = 0

        # Process pairs with concurrency control
        semaphore = asyncio.Semaphore(self.config.concurrent_limit)

        async def process_pair(pair: DocumentPair, trial: int) -> List[PairwiseResult]:
            nonlocal completed
            async with semaphore:
                results = await self.compare_pair(pair, trial)
                completed += len(results)
                if progress_callback:
                    try:
                        progress_callback(
                            completed,
                            total_comparisons,
                            pair.doc_id_1,
                            pair.doc_id_2,
                        )
                    except Exception:
                        pass
                return results

        # Create all comparison tasks
        tasks = []
        for pair in pairs:
            for trial in range(1, self.config.iterations + 1):
                tasks.append(process_pair(pair, trial))

        # Run all tasks
        for coro in asyncio.as_completed(tasks):
            results = await coro
            all_results.extend(results)

        # Get final ratings
        elo_ratings = self._elo.get_all_ratings()
        winner = self._elo.get_winner()

        return PairwiseSummary(
            total_comparisons=len(all_results),
            total_pairs=len(pairs),
            results=all_results,
            elo_ratings=elo_ratings,
            winner_doc_id=winner,
        )

    def filter_top_n(
        self,
        doc_ids: List[str],
        scores: Dict[str, float],
        n: Optional[int] = None,
    ) -> List[str]:
        """
        Filter to top N documents by score.

        Args:
            doc_ids: All document IDs
            scores: Dict mapping doc_id to score
            n: Number to keep (uses config.top_n if not provided)

        Returns:
            List of top N doc_ids
        """
        n = n or self.config.top_n
        if n is None or n >= len(doc_ids):
            return doc_ids

        # Sort by score descending
        sorted_ids = sorted(
            doc_ids,
            key=lambda d: scores.get(d, 0.0),
            reverse=True,
        )

        return sorted_ids[:n]

    def get_elo_rankings(self) -> List[EloRating]:
        """
        Get current Elo rankings.

        Returns:
            List of EloRating sorted by rating descending
        """
        return self._elo.get_all_ratings()

    def get_winner(self) -> Optional[str]:
        """
        Get the document with highest Elo rating.

        Returns:
            Doc ID of winner, or None if no comparisons done
        """
        return self._elo.get_winner()
