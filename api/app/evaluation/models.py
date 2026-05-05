"""
Evaluation data models for APICostX.

Defines all dataclasses and types used by the evaluation system.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any


class EvaluationType(str, Enum):
    """Types of evaluation supported."""
    SINGLE = "single"
    PAIRWISE = "pairwise"


@dataclass
class EvaluationCriterion:
    """
    A single evaluation criterion with name and description.

    Attributes:
        name: Short identifier (e.g., "factuality", "relevance")
        description: Full description including scoring guidance
    """
    name: str
    description: str

    def to_prompt_line(self) -> str:
        """Format criterion for inclusion in LLM prompt."""
        return f"- **{self.name}**: {self.description}"


@dataclass
class CriterionScore:
    """
    Score for a single criterion in a single-doc evaluation.

    Attributes:
        criterion: Name of the criterion
        score: Integer score (1-5)
        reason: Brief explanation from the judge
    """
    criterion: str
    score: int
    reason: str

    def __post_init__(self):
        # Convert 0 (N/A) to 1 - some LLMs return 0 for "not applicable" criteria
        if self.score == 0:
            self.score = 1
        if not 1 <= self.score <= 5:
            raise ValueError(f"Score must be 1-5, got {self.score}")


@dataclass
class SingleEvalResult:
    """
    Result of a single-document graded evaluation.

    Attributes:
        doc_id: Document identifier
        model: Judge model used
        trial: Trial number (for multi-iteration evaluation)
        scores: List of criterion scores
        timestamp: When evaluation was performed (legacy, use started_at/completed_at)
        started_at: When evaluation started
        completed_at: When evaluation completed
        duration_seconds: Duration in seconds
        raw_response: Optional raw LLM response for debugging
    """
    doc_id: str
    model: str
    trial: int
    scores: List[CriterionScore]
    timestamp: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    raw_response: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    tokens_thinking: Optional[int] = None

    @property
    def average_score(self) -> float:
        """Calculate average across all criteria."""
        if not self.scores:
            return 0.0
        return sum(s.score for s in self.scores) / len(self.scores)


@dataclass
class PairwiseResult:
    """
    Result of a pairwise comparison between two documents.

    Attributes:
        doc_id_1: First document identifier
        doc_id_2: Second document identifier
        winner_doc_id: ID of the winning document
        model: Judge model used
        trial: Trial number
        reason: Brief explanation from the judge
        timestamp: When comparison was performed (legacy, use started_at/completed_at)
        started_at: When evaluation started
        completed_at: When evaluation completed
        duration_seconds: Duration in seconds
        raw_response: Optional raw LLM response for debugging
    """
    doc_id_1: str
    doc_id_2: str
    winner_doc_id: str
    model: str
    trial: int
    reason: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    raw_response: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    tokens_thinking: Optional[int] = None

    def __post_init__(self):
        if self.winner_doc_id not in (self.doc_id_1, self.doc_id_2):
            raise ValueError(
                f"Winner '{self.winner_doc_id}' must be one of "
                f"'{self.doc_id_1}' or '{self.doc_id_2}'"
            )


@dataclass
class EloRating:
    """
    Elo rating for a document based on pairwise comparisons.

    Attributes:
        doc_id: Document identifier
        rating: Current Elo rating
        wins: Number of pairwise wins
        losses: Number of pairwise losses
        matches: Total number of matches
    """
    doc_id: str
    rating: float
    wins: int = 0
    losses: int = 0

    @property
    def matches(self) -> int:
        """Total matches played."""
        return self.wins + self.losses

    @property
    def win_rate(self) -> float:
        """Win rate as a percentage."""
        if self.matches == 0:
            return 0.0
        return (self.wins / self.matches) * 100


@dataclass
class EvaluationRun:
    """
    Metadata for an evaluation run.

    Attributes:
        run_id: Unique identifier for this eval run
        eval_type: Type of evaluation (single/pairwise)
        doc_ids: List of documents being evaluated
        criteria: List of criteria used
        iterations: Number of iterations per evaluation
        judge_models: List of judge models used
        started_at: When the run started
        completed_at: When the run completed (None if still running)
        status: Current status of the run
        config: Additional configuration used
    """
    run_id: str
    eval_type: EvaluationType
    doc_ids: List[str]
    criteria: List[str]
    iterations: int
    judge_models: List[str]
    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    status: str = "running"
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentRanking:
    """
    Ranking result for a document after evaluation.

    Attributes:
        doc_id: Document identifier
        rank: Position in ranking (1 = best)
        elo_rating: Elo rating from pairwise comparisons
        avg_score: Average score from single-doc evaluation
        wins: Pairwise wins
        losses: Pairwise losses
    """
    doc_id: str
    rank: int
    elo_rating: Optional[float] = None
    avg_score: Optional[float] = None
    wins: int = 0
    losses: int = 0

    @property
    def summary(self) -> str:
        """Human-readable ranking summary."""
        parts = [f"#{self.rank}: {self.doc_id}"]
        if self.elo_rating is not None:
            parts.append(f"Elo={self.elo_rating:.0f}")
        if self.avg_score is not None:
            parts.append(f"Avg={self.avg_score:.2f}")
        if self.wins or self.losses:
            parts.append(f"W/L={self.wins}/{self.losses}")
        return " | ".join(parts)


@dataclass
class EvaluationSummary:
    """
    Summary of a complete evaluation run.

    Attributes:
        run_id: Evaluation run identifier
        rankings: Ordered list of document rankings
        winner_doc_id: ID of the winning document
        total_evaluations: Total number of evaluations performed
        duration_seconds: Total time taken
    """
    run_id: str
    rankings: List[DocumentRanking]
    winner_doc_id: str
    total_evaluations: int
    duration_seconds: float

    @property
    def winner(self) -> Optional[DocumentRanking]:
        """Get the winning document ranking."""
        if self.rankings:
            return self.rankings[0]
        return None
