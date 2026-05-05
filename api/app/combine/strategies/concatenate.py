"""
Concatenate strategy - simple join of winner reports.

This is the simplest combine strategy that just concatenates
all winner reports with clear separators between them.
"""
from . import CombineStrategy, CombineStrategyType, CombineInput, CombineResult


class ConcatenateStrategy(CombineStrategy):
    """
    Simple concatenation of winner reports.

    Joins all input reports with markdown separators,
    optionally adding headers with model/score info.
    """

    def __init__(self, include_headers: bool = True, separator: str = "\n\n---\n\n"):
        self._include_headers = include_headers
        self._separator = separator

    @property
    def strategy_type(self) -> CombineStrategyType:
        return CombineStrategyType.CONCATENATE

    @property
    def display_name(self) -> str:
        return "Concatenate"

    @property
    def requires_llm(self) -> bool:
        return False

    async def combine(self, input: CombineInput) -> CombineResult:
        """Concatenate all reports with separators."""
        if not input.reports:
            return CombineResult(
                content="",
                strategy_used=self.strategy_type,
                input_report_count=0,
                output_length=0,
            )

        parts = []

        for i, report in enumerate(input.reports):
            if self._include_headers:
                header_parts = [f"## Report {i + 1}"]

                # Add model info if available
                if i < len(input.report_models) and input.report_models[i]:
                    header_parts.append(f"Model: {input.report_models[i]}")

                # Add score if available
                if i < len(input.report_scores):
                    header_parts.append(f"Score: {input.report_scores[i]:.2f}")

                header = " | ".join(header_parts)
                parts.append(f"{header}\n\n{report}")
            else:
                parts.append(report)

        combined = self._separator.join(parts)

        return CombineResult(
            content=combined,
            strategy_used=self.strategy_type,
            input_report_count=len(input.reports),
            output_length=len(combined),
            metadata={
                "include_headers": self._include_headers,
            },
        )
