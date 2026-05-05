"""
Combine service - orchestrates the combine phase.

Main entry point for combining winner reports into
a final "gold standard" document.
"""
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from .source_handler import SourceHandler, WinnerReport
from .strategies import (
    CombineStrategy,
    CombineStrategyType,
    CombineInput,
    CombineResult,
    ConcatenateStrategy,
    BestOfNStrategy,
    IntelligentMergeStrategy,
    SectionAssemblyStrategy,
)

logger = logging.getLogger(__name__)


@dataclass
class CombineConfig:
    """Configuration for the combine phase."""
    enabled: bool = True
    strategy: CombineStrategyType = CombineStrategyType.INTELLIGENT_MERGE
    top_n: int = 2  # Number of winners to combine

    # Model settings (for intelligent merge) - REQUIRED from preset
    provider: str = ""
    model: str = ""
    reasoning_effort: str = "medium"
    max_completion_tokens: int = 50000

    # Output settings
    output_dir: Optional[str] = None
    output_filename_prefix: str = "combined"


class CombineService:
    """
    Main service for the Combine Phase.

    Orchestrates loading winner reports, selecting a strategy,
    and producing the combined output.
    """

    def __init__(self, config: Optional[CombineConfig] = None):
        self.config = config or CombineConfig()
        self._strategies: dict[CombineStrategyType, CombineStrategy] = {}
        self._init_strategies()

    def _init_strategies(self) -> None:
        """Initialize available strategies."""
        self._strategies[CombineStrategyType.CONCATENATE] = ConcatenateStrategy()
        self._strategies[CombineStrategyType.BEST_OF_N] = BestOfNStrategy()
        self._strategies[CombineStrategyType.SECTION_ASSEMBLY] = SectionAssemblyStrategy()

        # Intelligent merge with config settings
        self._strategies[CombineStrategyType.INTELLIGENT_MERGE] = IntelligentMergeStrategy(
            provider=self.config.provider,
            model=self.config.model,
            reasoning_effort=self.config.reasoning_effort,
            max_completion_tokens=self.config.max_completion_tokens,
        )

    def get_strategy(self, strategy_type: CombineStrategyType) -> CombineStrategy:
        """Get a strategy instance by type."""
        if strategy_type not in self._strategies:
            raise ValueError(f"Unknown strategy: {strategy_type}")
        return self._strategies[strategy_type]

    async def combine_from_db(
        self,
        db_path: str,
        output_folder: str,
        original_instructions: str = "",
        original_query: str = "",
        user_uuid: Optional[str] = None,
        strategy: Optional[CombineStrategyType] = None,
    ) -> CombineResult:
        """
        Combine top reports from an evaluation database.

        Args:
            db_path: Path to evaluation SQLite database
            output_folder: Directory containing report files
            original_instructions: Original instructions for context
            original_query: Original query for context
            strategy: Override strategy (uses config default if None)

        Returns:
            CombineResult with combined content
        """
        if not self.config.enabled:
            logger.info("Combine phase is disabled")
            return CombineResult(
                content="",
                strategy_used=strategy or self.config.strategy,
                metadata={"disabled": True},
            )

        # Load winner reports
        handler = SourceHandler(db_path, output_folder)
        winners = handler.get_top_reports(limit=self.config.top_n)

        if not winners:
            logger.warning("No winner reports found to combine")
            return CombineResult(
                content="",
                strategy_used=strategy or self.config.strategy,
                metadata={"error": "No winners found"},
            )

        logger.info(f"Found {len(winners)} winner reports to combine")

        # Build combine input
        combine_input = CombineInput(
            user_uuid=user_uuid,
            reports=[w.content for w in winners],
            report_paths=[w.file_path for w in winners],
            original_instructions=original_instructions,
            original_query=original_query,
            report_scores=[w.score for w in winners],
            report_models=[w.model or "" for w in winners],
        )

        return await self.combine(combine_input, strategy)

    async def combine(
        self,
        input: CombineInput,
        strategy: Optional[CombineStrategyType] = None,
    ) -> CombineResult:
        """
        Combine reports using the specified strategy.

        Args:
            input: CombineInput with reports and context
            strategy: Strategy to use (uses config default if None)

        Returns:
            CombineResult with combined content
        """
        strategy_type = strategy or self.config.strategy
        strategy_impl = self.get_strategy(strategy_type)

        logger.info(f"Running combine with strategy: {strategy_impl.display_name}")

        result = await strategy_impl.combine(input)

        logger.info(
            f"Combine complete: {result.input_report_count} reports -> "
            f"{result.output_length} chars"
        )

        return result

    async def combine_reports(
        self,
        report_contents: list[str],
        original_instructions: str = "",
        original_query: str = "",
        report_scores: Optional[list[float]] = None,
        report_models: Optional[list[str]] = None,
        user_uuid: Optional[str] = None,
        strategy: Optional[CombineStrategyType] = None,
    ) -> CombineResult:
        """
        Combine reports directly from content.

        Convenience method that doesn't require a database.

        Args:
            report_contents: List of report content strings
            original_instructions: Original instructions for context
            original_query: Original query for context
            report_scores: Optional scores for each report
            report_models: Optional model names for each report
            strategy: Strategy to use (uses config default if None)

        Returns:
            CombineResult with combined content
        """
        combine_input = CombineInput(
            user_uuid=user_uuid,
            reports=report_contents,
            original_instructions=original_instructions,
            original_query=original_query,
            report_scores=report_scores or [],
            report_models=report_models or [],
        )

        return await self.combine(combine_input, strategy)

    def save_result(
        self,
        result: CombineResult,
        output_dir: str,
        base_name: str = "combined",
    ) -> str:
        """
        Save combine result to a file.

        Args:
            result: CombineResult to save
            output_dir: Directory to save to
            base_name: Base filename

        Returns:
            Path to saved file
        """
        os.makedirs(output_dir, exist_ok=True)

        # Build filename
        strategy_suffix = result.strategy_used.value
        model_suffix = result.model_used or "unknown"
        filename = f"{base_name}.{strategy_suffix}.{model_suffix}.md"

        filepath = os.path.join(output_dir, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(result.content)

        logger.info(f"Saved combined report to: {filepath}")
        return filepath
