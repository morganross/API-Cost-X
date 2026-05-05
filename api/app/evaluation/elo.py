"""
Elo rating calculation for pairwise comparisons.

Implements standard Elo rating system for ranking documents
based on head-to-head comparison results.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .models import EloRating, PairwiseResult


# Default Elo parameters
DEFAULT_K_FACTOR = 32.0  # Rating adjustment speed
DEFAULT_INITIAL_RATING = 1000.0  # Starting rating for new documents


@dataclass
class EloConfig:
    """Configuration for Elo rating calculation."""

    k_factor: float = DEFAULT_K_FACTOR
    initial_rating: float = DEFAULT_INITIAL_RATING

    # Dynamic K-factor settings (optional)
    use_dynamic_k: bool = False
    high_rating_threshold: float = 1200.0
    high_rating_k_factor: float = 16.0  # Lower K for established players


class EloCalculator:
    """
    Elo rating calculator for document comparisons.

    Uses standard Elo formula:
    - Expected score: E = 1 / (1 + 10^((R_opponent - R_self) / 400))
    - New rating: R' = R + K * (S - E)

    Where S is actual score (1 for win, 0 for loss, 0.5 for draw).
    """

    def __init__(self, config: Optional[EloConfig] = None):
        """
        Initialize calculator.

        Args:
            config: Elo calculation configuration
        """
        self.config = config or EloConfig()
        self._ratings: Dict[str, float] = {}
        self._wins: Dict[str, int] = {}
        self._losses: Dict[str, int] = {}

    def _get_k_factor(self, doc_id: str) -> float:
        """
        Get K-factor for a document.

        With dynamic K, established high-rated documents get lower K.
        """
        if not self.config.use_dynamic_k:
            return self.config.k_factor

        rating = self._ratings.get(doc_id, self.config.initial_rating)
        if rating >= self.config.high_rating_threshold:
            return self.config.high_rating_k_factor
        return self.config.k_factor

    def _ensure_doc(self, doc_id: str) -> None:
        """Ensure document exists in ratings."""
        if doc_id not in self._ratings:
            self._ratings[doc_id] = self.config.initial_rating
            self._wins[doc_id] = 0
            self._losses[doc_id] = 0

    def expected_score(self, rating_a: float, rating_b: float) -> float:
        """
        Calculate expected score for player A against player B.

        Args:
            rating_a: Rating of player A
            rating_b: Rating of player B

        Returns:
            Expected score (0-1) for player A
        """
        return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))

    def update_ratings(
        self,
        doc_id_1: str,
        doc_id_2: str,
        winner_doc_id: str,
    ) -> Tuple[float, float]:
        """
        Update ratings after a match.

        Args:
            doc_id_1: First document ID
            doc_id_2: Second document ID
            winner_doc_id: ID of the winning document

        Returns:
            Tuple of (new_rating_1, new_rating_2)
        """
        self._ensure_doc(doc_id_1)
        self._ensure_doc(doc_id_2)

        r1 = self._ratings[doc_id_1]
        r2 = self._ratings[doc_id_2]

        # Calculate expected scores
        e1 = self.expected_score(r1, r2)
        e2 = 1.0 - e1

        # Actual scores
        if winner_doc_id == doc_id_1:
            s1, s2 = 1.0, 0.0
            self._wins[doc_id_1] += 1
            self._losses[doc_id_2] += 1
        elif winner_doc_id == doc_id_2:
            s1, s2 = 0.0, 1.0
            self._wins[doc_id_2] += 1
            self._losses[doc_id_1] += 1
        else:
            # Tie (shouldn't happen in our system, but handle it)
            s1 = s2 = 0.5

        # Get K-factors
        k1 = self._get_k_factor(doc_id_1)
        k2 = self._get_k_factor(doc_id_2)

        # Update ratings
        self._ratings[doc_id_1] = r1 + k1 * (s1 - e1)
        self._ratings[doc_id_2] = r2 + k2 * (s2 - e2)

        return self._ratings[doc_id_1], self._ratings[doc_id_2]

    def process_result(self, result: PairwiseResult) -> Tuple[float, float]:
        """
        Process a single pairwise result.

        Args:
            result: Pairwise comparison result

        Returns:
            Updated ratings for both documents
        """
        return self.update_ratings(
            result.doc_id_1,
            result.doc_id_2,
            result.winner_doc_id,
        )

    def process_results(self, results: List[PairwiseResult]) -> None:
        """
        Process multiple pairwise results in order.

        Args:
            results: List of pairwise results to process
        """
        for result in results:
            self.process_result(result)

    def get_rating(self, doc_id: str) -> EloRating:
        """
        Get current rating for a document.

        Args:
            doc_id: Document identifier

        Returns:
            EloRating object with current stats
        """
        self._ensure_doc(doc_id)
        return EloRating(
            doc_id=doc_id,
            rating=self._ratings[doc_id],
            wins=self._wins.get(doc_id, 0),
            losses=self._losses.get(doc_id, 0),
        )

    def get_all_ratings(self) -> List[EloRating]:
        """
        Get ratings for all documents.

        Returns:
            List of EloRating objects sorted by rating descending
        """
        ratings = []
        for doc_id in self._ratings:
            ratings.append(self.get_rating(doc_id))

        ratings.sort(key=lambda x: x.rating, reverse=True)
        return ratings

    def get_rankings(self) -> List[Tuple[str, float]]:
        """
        Get document rankings by Elo rating.

        Returns:
            List of (doc_id, rating) tuples sorted by rating descending
        """
        items = list(self._ratings.items())
        items.sort(key=lambda x: x[1], reverse=True)
        return items

    def get_top_n(self, n: int) -> List[str]:
        """
        Get top N document IDs by rating.

        Args:
            n: Number of top documents to return

        Returns:
            List of top N doc_ids
        """
        rankings = self.get_rankings()
        return [doc_id for doc_id, _ in rankings[:n]]

    def get_winner(self) -> Optional[str]:
        """
        Get the document with highest rating.

        Returns:
            Doc ID of highest rated document, or None if no ratings
        """
        if not self._ratings:
            return None
        return max(self._ratings.items(), key=lambda x: x[1])[0]

    def reset(self) -> None:
        """Reset all ratings to initial state."""
        self._ratings.clear()
        self._wins.clear()
        self._losses.clear()

    def to_dict(self) -> Dict[str, Dict]:
        """
        Export current state as dictionary.

        Returns:
            Dict with ratings, wins, losses for each document
        """
        return {
            doc_id: {
                "rating": self._ratings[doc_id],
                "wins": self._wins.get(doc_id, 0),
                "losses": self._losses.get(doc_id, 0),
            }
            for doc_id in self._ratings
        }

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Dict],
        config: Optional[EloConfig] = None,
    ) -> "EloCalculator":
        """
        Create calculator from exported dictionary.

        Args:
            data: Dict with doc_id -> {rating, wins, losses}
            config: Optional configuration

        Returns:
            EloCalculator with restored state
        """
        calc = cls(config)
        for doc_id, stats in data.items():
            calc._ratings[doc_id] = stats.get("rating", calc.config.initial_rating)
            calc._wins[doc_id] = stats.get("wins", 0)
            calc._losses[doc_id] = stats.get("losses", 0)
        return calc


def compute_elo_from_results(
    results: List[PairwiseResult],
    k_factor: float = DEFAULT_K_FACTOR,
    initial_rating: float = DEFAULT_INITIAL_RATING,
) -> Dict[str, float]:
    """
    Convenience function to compute Elo ratings from pairwise results.

    Args:
        results: List of pairwise comparison results
        k_factor: Elo K-factor
        initial_rating: Starting rating for new documents

    Returns:
        Dict mapping doc_id to final Elo rating
    """
    config = EloConfig(k_factor=k_factor, initial_rating=initial_rating)
    calc = EloCalculator(config)
    calc.process_results(results)
    return dict(calc._ratings)
