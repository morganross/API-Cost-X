"""
Combine strategies for merging winner reports.

Each strategy implements a different approach to combining top-ranked
documents from the evaluation phase into a final "gold standard" output.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class CombineStrategyType(str, Enum):
    """Available combine strategies."""
    CONCATENATE = "concatenate"
    BEST_OF_N = "best_of_n"
    INTELLIGENT_MERGE = "intelligent_merge"
    SECTION_ASSEMBLY = "section_assembly"


@dataclass
class CombineInput:
    """Input for a combine operation."""
    # User context
    user_uuid: Optional[str] = None

    # Winner reports (content)
    reports: list[str]
    report_paths: list[str] = field(default_factory=list)

    # Original context
    original_instructions: str = ""
    original_query: str = ""

    # Metadata
    report_scores: list[float] = field(default_factory=list)
    report_models: list[str] = field(default_factory=list)


@dataclass
class CombineResult:
    """Result from a combine operation."""
    content: str
    strategy_used: CombineStrategyType

    # Metrics
    input_report_count: int = 0
    output_length: int = 0

    model_used: Optional[str] = None

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)


class CombineStrategy(ABC):
    """
    Abstract base class for combine strategies.

    Each strategy takes multiple winner reports and produces
    a single combined output using different approaches.
    """

    @property
    @abstractmethod
    def strategy_type(self) -> CombineStrategyType:
        """Return the strategy type identifier."""
        pass

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name for UI display."""
        pass

    @property
    def requires_llm(self) -> bool:
        """Whether this strategy requires an LLM call."""
        return False

    @abstractmethod
    async def combine(self, input: CombineInput) -> CombineResult:
        """
        Combine the input reports into a single output.

        Args:
            input: CombineInput with reports and context

        Returns:
            CombineResult with combined content
        """
        pass


# Re-export strategy implementations
from .concatenate import ConcatenateStrategy
from .best_of_n import BestOfNStrategy
from .intelligent_merge import IntelligentMergeStrategy
from .section_assembly import SectionAssemblyStrategy

__all__ = [
    "CombineStrategy",
    "CombineStrategyType",
    "CombineInput",
    "CombineResult",
    "ConcatenateStrategy",
    "BestOfNStrategy",
    "IntelligentMergeStrategy",
    "SectionAssemblyStrategy",
]
