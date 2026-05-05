"""
APICostX Evaluation Module.

Provides document evaluation through:
- Single-document graded evaluation (scores per criterion)
- Pairwise comparison (head-to-head battles)
- Elo rating calculation for ranking

Usage:
    from app.evaluation import (
        EvaluationService,
        EvaluationConfig,
        EvaluationInput,
        DocumentInput,
    )

    # Create service
    service = EvaluationService(EvaluationConfig(
        iterations=2,
        judge_models=["openai:your-model-here"],  # REQUIRED from preset
        enable_pairwise=True,
        pairwise_top_n=5,
    ))

    # Prepare input
    input_data = EvaluationInput(documents=[
        DocumentInput(doc_id="doc1.md", content="..."),
        DocumentInput(doc_id="doc2.md", content="..."),
    ])

    # Run evaluation
    result = await service.evaluate(input_data)
    print(f"Winner: {result.winner_doc_id}")
"""

# Models
from .models import (
    EvaluationType,
    EvaluationCriterion,
    CriterionScore,
    SingleEvalResult,
    PairwiseResult,
    EloRating,
    EvaluationRun,
    DocumentRanking,
    EvaluationSummary,
)

# Criteria
from .criteria import (
    CriteriaManager,
    load_criteria_from_yaml,
    save_criteria_to_yaml,
    format_criteria_for_prompt,
)

# Judge
from .judge import (
    Judge,
    JudgeConfig,
    FpfStatsTracker,
)

# Single-doc evaluation
from .single_doc import (
    SingleDocEvaluator,
    SingleEvalConfig,
    SingleEvalSummary,
    DocumentInput,
)

# Pairwise evaluation
from .pairwise import (
    PairwiseEvaluator,
    PairwiseConfig,
    PairwiseSummary,
    DocumentPair,
)

# Elo calculation
from .elo import (
    EloCalculator,
    EloConfig,
    compute_elo_from_results,
)

# Main service
from .service import (
    EvaluationService,
    EvaluationConfig,
    EvaluationInput,
    EvaluationResult,
)


__all__ = [
    # Models
    "EvaluationType",
    "EvaluationCriterion",
    "CriterionScore",
    "SingleEvalResult",
    "PairwiseResult",
    "EloRating",
    "EvaluationRun",
    "DocumentRanking",
    "EvaluationSummary",
    # Criteria
    "CriteriaManager",
    "load_criteria_from_yaml",
    "save_criteria_to_yaml",
    "format_criteria_for_prompt",
    # Judge
    "Judge",
    "JudgeConfig",
    "FpfStatsTracker",
    # Single-doc
    "SingleDocEvaluator",
    "SingleEvalConfig",
    "SingleEvalSummary",
    "DocumentInput",
    # Pairwise
    "PairwiseEvaluator",
    "PairwiseConfig",
    "PairwiseSummary",
    "DocumentPair",
    # Elo
    "EloCalculator",
    "EloConfig",
    "compute_elo_from_results",
    # Service
    "EvaluationService",
    "EvaluationConfig",
    "EvaluationInput",
    "EvaluationResult",
]
