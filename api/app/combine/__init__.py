"""
Combine Phase - merges winner reports into gold standard documents.

The Combine Phase takes top-ranked documents from the Evaluation Phase
and merges them into a single high-quality "gold standard" output.

Available strategies:
- CONCATENATE: Simple join of reports with separators
- BEST_OF_N: Select the single best report
- INTELLIGENT_MERGE: LLM-powered synthesis (default)
- SECTION_ASSEMBLY: Best section from each report

Example usage:

    from app.combine import CombineService, CombineConfig, CombineStrategyType

    # Configure the combine phase (model values from preset)
    config = CombineConfig(
        strategy=CombineStrategyType.INTELLIGENT_MERGE,
        provider=preset_provider,  # from preset config
        model=preset_model,  # from preset config
        top_n=2,
    )

    # Create service
    service = CombineService(config)

    # Combine from evaluation database
    result = await service.combine_from_db(
        db_path="path/to/eval.db",
        output_folder="path/to/reports/",
        original_instructions="Write a report about...",
    )

    # Or combine reports directly
    result = await service.combine_reports(
        report_contents=[report1, report2],
        original_query="Research question",
    )

    # Save the result
    filepath = service.save_result(result, output_dir="output/")
"""

from .service import CombineService, CombineConfig
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

__all__ = [
    # Main service
    "CombineService",
    "CombineConfig",
    # Source handling
    "SourceHandler",
    "WinnerReport",
    # Strategy types
    "CombineStrategy",
    "CombineStrategyType",
    "CombineInput",
    "CombineResult",
    # Strategy implementations
    "ConcatenateStrategy",
    "BestOfNStrategy",
    "IntelligentMergeStrategy",
    "SectionAssemblyStrategy",
]
