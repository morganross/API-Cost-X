"""
Intelligent merge strategy - LLM-powered report combination.

This is the primary combine strategy that uses an LLM to
intelligently merge multiple winner reports into a single
high-quality "gold standard" document.
"""
import logging
from typing import Optional

from . import CombineStrategy, CombineStrategyType, CombineInput, CombineResult

logger = logging.getLogger(__name__)

# Note: Combine instructions must now be provided from Content Library via preset.
# No default prompt is defined here - this enforces explicit configuration.


class IntelligentMergeStrategy(CombineStrategy):
    """
    LLM-powered intelligent merge of winner reports.

    Uses the FPF adapter to call an LLM that synthesizes
    multiple reports into a single high-quality output.
    """

    def __init__(
        self,
        provider: str = "",  # REQUIRED - must be set by caller
        model: str = "",  # REQUIRED - must be set by caller
        combine_prompt: Optional[str] = None,  # REQUIRED from Content Library
        reasoning_effort: str = "medium",
        max_completion_tokens: int = 50000,
    ):
        self._provider = provider
        self._model = model
        self._combine_prompt = combine_prompt  # No fallback - must be provided from preset
        self._reasoning_effort = reasoning_effort
        self._max_completion_tokens = max_completion_tokens

        # Will be set when FPF adapter is available
        self._fpf_adapter = None

    @property
    def strategy_type(self) -> CombineStrategyType:
        return CombineStrategyType.INTELLIGENT_MERGE

    @property
    def display_name(self) -> str:
        return "Intelligent Merge"

    @property
    def requires_llm(self) -> bool:
        return True

    def set_fpf_adapter(self, adapter) -> None:
        """Set the FPF adapter for LLM calls."""
        self._fpf_adapter = adapter

    def _build_merge_prompt(self, input: CombineInput) -> str:
        """Build the full prompt for the LLM."""
        parts = [self._combine_prompt, ""]

        # Add original instructions if available
        if input.original_instructions:
            parts.append("--- ORIGINAL INSTRUCTIONS ---")
            parts.append(input.original_instructions)
            parts.append("")

        # Add original query if available
        if input.original_query:
            parts.append("--- ORIGINAL QUERY ---")
            parts.append(input.original_query)
            parts.append("")

        # Add each report
        for i, report in enumerate(input.reports):
            model_info = ""
            if i < len(input.report_models) and input.report_models[i]:
                model_info = f" (from {input.report_models[i]})"

            score_info = ""
            if i < len(input.report_scores):
                score_info = f" [Score: {input.report_scores[i]:.2f}]"

            parts.append(f"--- REPORT {i + 1}{model_info}{score_info} ---")
            parts.append(report)
            parts.append("")

        parts.append("--- END OF INPUTS ---")
        parts.append("Please generate the combined Gold Standard report now.")

        return "\n".join(parts)

    async def combine(self, input: CombineInput) -> CombineResult:
        """Use LLM to intelligently merge reports."""
        if not input.reports:
            return CombineResult(
                content="",
                strategy_used=self.strategy_type,
                input_report_count=0,
                output_length=0,
            )

        # If only one report, just return it
        if len(input.reports) == 1:
            logger.info("Only one report provided, returning as-is")
            return CombineResult(
                content=input.reports[0],
                strategy_used=self.strategy_type,
                input_report_count=1,
                output_length=len(input.reports[0]),
                metadata={"single_report_passthrough": True},
            )

        # Check if FPF adapter is available
        if self._fpf_adapter is None:
            # Lazy import to avoid circular dependency
            from ...adapters.fpf import FpfAdapter
            self._fpf_adapter = FpfAdapter()

        # Build the merge prompt
        merge_prompt = self._build_merge_prompt(input)

        # Build config for FPF
        from ...adapters.base import GenerationConfig

        config = GenerationConfig(
            provider=self._provider,
            model=self._model,
            extra={
                "reasoning_effort": self._reasoning_effort,
                "max_completion_tokens": self._max_completion_tokens,
            },
        )

        logger.info(f"Running intelligent merge with {self._provider}/{self._model}")

        try:
            # Call FPF adapter
            result = await self._fpf_adapter.generate(
                query=merge_prompt,
                config=config,
                user_uuid=input.user_uuid,
                document_content="Merge the following reports into a Gold Standard document.",
            )

            if result.status.value == "completed":
                return CombineResult(
                    content=result.content,
                    strategy_used=self.strategy_type,
                    input_report_count=len(input.reports),
                    output_length=len(result.content),
                    model_used=self._model,
                    metadata={
                        "provider": self._provider,
                        "reasoning_effort": self._reasoning_effort,
                        "fpf_duration_seconds": result.duration_seconds,
                    },
                )
            logger.error(f"FPF merge failed: {result.error_message}")
            raise RuntimeError(f"Intelligent merge failed: {result.error_message}")

        except Exception as e:
            logger.error("Intelligent merge failed: %s", type(e).__name__)
            raise RuntimeError(f"Intelligent merge failed: {e}") from e
