"""
Best-of-N strategy - select the single best report.

This strategy simply returns the highest-scoring report
from the input set without any merging.
"""
from . import CombineStrategy, CombineStrategyType, CombineInput, CombineResult


class BestOfNStrategy(CombineStrategy):
    """
    Select the best report from the input set.

    Returns the highest-scoring report unchanged.
    Useful when you want to skip merging and just
    pick the winner.
    """

    @property
    def strategy_type(self) -> CombineStrategyType:
        return CombineStrategyType.BEST_OF_N

    @property
    def display_name(self) -> str:
        return "Best of N"

    @property
    def requires_llm(self) -> bool:
        return False

    async def combine(self, input: CombineInput) -> CombineResult:
        """Select the best report based on scores."""
        if not input.reports:
            return CombineResult(
                content="",
                strategy_used=self.strategy_type,
                input_report_count=0,
                output_length=0,
            )

        # If we have scores, pick the highest
        if input.report_scores and len(input.report_scores) == len(input.reports):
            best_idx = max(range(len(input.report_scores)),
                          key=lambda i: input.report_scores[i])
        else:
            # Default to first report (assumed to be pre-sorted by score)
            best_idx = 0

        best_report = input.reports[best_idx]
        best_model = input.report_models[best_idx] if best_idx < len(input.report_models) else None
        best_score = input.report_scores[best_idx] if best_idx < len(input.report_scores) else None

        return CombineResult(
            content=best_report,
            strategy_used=self.strategy_type,
            input_report_count=len(input.reports),
            output_length=len(best_report),
            model_used=best_model,
            metadata={
                "selected_index": best_idx,
                "selected_score": best_score,
                "selected_model": best_model,
            },
        )
