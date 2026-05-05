"""
Run Executor Service.

Orchestrates the full run pipeline:
1. Generation Phase - Create documents using FPF/GPTR
2. Single-Doc Evaluation - Grade each doc immediately after generation (STREAMING)
3. Pairwise Evaluation - Compare docs head-to-head (BATCH, after all single evals)
4. Combine Phase - Merge winners (optional)
5. Post-Combine Evaluation (optional)
"""

import asyncio
import json
import logging
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from ..adapters.fpf.adapter import FpfAdapter
from ..adapters.gptr.adapter import GptrAdapter
from ..adapters.dr.adapter import DrAdapter
from ..adapters.aiq.adapter import AiqAdapter
from ..adapters.base import GeneratorType
from .output_writer import OutputWriter
from ..evaluation import (
    SingleEvalSummary,
    PairwiseSummary,
    FpfStatsTracker,
)
from ..evaluation.single_doc import EvalCompleteCallback
from ..infra.db.repositories import RunRepository
from ..infra.db.session import get_user_session_by_uuid

# Callback fired after each document generation completes
# Args: (doc_id, model, generator, source_doc_id, iteration, file_path, duration_seconds, started_at)
OnGenCompleteCallback = Callable[..., Awaitable[None]]


class RunPhase(str, Enum):
    """Current phase of run execution."""
    PENDING = "pending"
    GENERATING = "generating"
    SINGLE_EVAL = "single_eval"
    PAIRWISE_EVAL = "pairwise_eval"
    COMBINING = "combining"
    POST_COMBINE_EVAL = "post_combine_eval"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"  # Some source docs failed, others succeeded
    FAILED = "failed"
    CANCELLED = "cancelled"



def _prepend_frontmatter(content: str, source_content: str) -> str:
    """Prepend frontmatter from source_content onto content.
    - If source has a YAML frontmatter block (---...---), copy it verbatim.
    - Otherwise build one using the first non-empty line as the title.
    - Skips injection if content already starts with a frontmatter block."""
    if content.startswith('---'):
        return content
    src = source_content.strip()
    if src.startswith('---'):
        # Extract the full frontmatter block from the source
        end = src.find('---', 3)
        if end != -1:
            frontmatter = src[:end + 3].strip()
            return frontmatter + '\n\n' + content
    # Fallback: build frontmatter from first non-empty line
    first_line = ''
    for line in source_content.splitlines():
        stripped = line.strip()
        if stripped and stripped != '---':
            first_line = stripped.lstrip('#').strip()
            break
    if not first_line:
        return content
    return '---\ntitle: ' + first_line + '\n---\n\n' + content

@dataclass
class GeneratedDocument:
    """A document produced by generation."""
    doc_id: str
    content: str
    generator: GeneratorType
    model: str
    source_doc_id: str  # The input document ID
    iteration: int
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    tokens_thinking: Optional[int] = None
    duration_seconds: float = 0.0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RunConfig:
    """Configuration for a run. All fields are REQUIRED unless explicitly Optional."""

    # User Context - REQUIRED for local execution system
    user_uuid: str  # User UUID for fetching provider API keys from the root .env

    # Inputs - REQUIRED (no defaults)
    document_ids: List[str]
    document_contents: Dict[str, str]  # doc_id -> content

    # Generators - REQUIRED (no defaults)
    generators: List[GeneratorType]
    models: List[str]  # Model names to use (legacy, used if per-generator not set)
    model_settings: Dict[str, Dict[str, Any]]  # REQUIRED per-model settings

    # Iterations - REQUIRED (no defaults)
    iterations: int
    eval_iterations: int

    # Concurrency settings - REQUIRED (no defaults)
    generation_concurrency: int
    eval_concurrency: int
    request_timeout: Optional[int]
    eval_timeout: Optional[int]

    # Logging - REQUIRED (no defaults)
    save_run_logs: bool

    # Per-generator model lists (override global models if set) - Optional with defaults
    fpf_models: Optional[List[str]] = None
    gptr_models: Optional[List[str]] = None
    dr_models: Optional[List[str]] = None
    aiq_models: Optional[List[str]] = None

    # Phase configs - carried through so runtime behavior matches saved preset state
    general_config: Dict[str, Any] = field(default_factory=dict)
    fpf_config: Dict[str, Any] = field(default_factory=dict)
    gptr_config: Dict[str, Any] = field(default_factory=dict)
    dr_config: Dict[str, Any] = field(default_factory=dict)
    ma_config: Dict[str, Any] = field(default_factory=dict)
    aiq_config: Dict[str, Any] = field(default_factory=dict)
    eval_config: Dict[str, Any] = field(default_factory=dict)
    pairwise_config: Dict[str, Any] = field(default_factory=dict)
    combine_config: Dict[str, Any] = field(default_factory=dict)
    concurrency_config: Dict[str, Any] = field(default_factory=dict)

    # Instructions - Validated based on enabled features (optional but validated in __post_init__)
    instructions: Optional[str] = None  # REQUIRED if FPF generator enabled

    # Expose evaluation criteria to generators (helps them know what they're judged on)
    expose_criteria_to_generators: bool = False

    # Evaluation - Defaults provided
    enable_single_eval: bool = True
    enable_pairwise: bool = True
    eval_judge_models: List[str] = field(default_factory=list)  # REQUIRED when eval enabled
    eval_retries: int = 0
    eval_temperature: Optional[float] = None
    eval_max_tokens: Optional[int] = None
    eval_thinking_budget_tokens: Optional[int] = None
    # NOTE: eval_enable_grounding removed - FPF always uses grounding, non-configurable
    pairwise_top_n: Optional[int] = None  # Optional top-N filtering

    # Custom evaluation instructions (from Content Library) - Validated based on enabled features
    single_eval_instructions: Optional[str] = None  # REQUIRED if enable_single_eval
    pairwise_eval_instructions: Optional[str] = None  # REQUIRED if enable_pairwise
    eval_criteria: Optional[str] = None  # REQUIRED if any eval enabled

    # Combine - Validated if enabled
    enable_combine: bool = False
    combine_strategy: str = ""  # REQUIRED if combine enabled
    combine_models: List[str] = field(default_factory=list)  # REQUIRED if combine enabled
    combine_instructions: Optional[str] = None  # REQUIRED if combine enabled
    combine_max_tokens: Optional[int] = None  # Max output tokens for combine phase

    # FPF Retry settings - Defaults provided
    fpf_max_retries: int = 3  # Max retries within FPF for API errors
    fpf_retry_delay: float = 1.0  # Seconds between FPF retry attempts

    # Post-Combine Configuration - Optional
    post_combine_top_n: Optional[int] = None  # Optional limit for post-combine eval

    # Output Configuration - Where to write the winning document
    output_destination: str = "none"  # "none", "library", or "github"
    output_filename_template: str = "{preset_name}_{timestamp}_winner"
    github_connection_id: Optional[str] = None  # GitHub connection ID for pushing output
    github_output_path: Optional[str] = None  # Path in GitHub repo for output (e.g., "/outputs")
    github_commit_message: str = "APICostX output: {filename}"
    prepend_source_first_line_frontmatter: bool = False  # Prepend source first line as YAML frontmatter
    key_mode: str = field(default='byok')  # REQUIRED: resolved at run start. 'byok'=user keys, 'system'=platform keys
    preset_id: Optional[str] = None  # For output filename template
    preset_name: Optional[str] = None  # For output filename template

    # Document names for UI display (doc_id -> human-readable name)
    document_names: Dict[str, str] = field(default_factory=dict)

    # Document relative paths for folder mirroring (doc_id -> relative path from input root)
    document_relative_paths: Dict[str, str] = field(default_factory=dict)

    # Max concurrent pipelines (limits how many source docs process simultaneously)
    max_concurrent_pipelines: int = 5

    # Callbacks
    on_progress: Optional[Callable[[str, float, str], None]] = None
    on_gen_complete: Optional[OnGenCompleteCallback] = None  # Fires after each doc generation
    on_eval_complete: Optional[EvalCompleteCallback] = None  # Fires after each judge eval
    # Called when a task is served from resume cache — same signature as on_gen_complete
    # but only increments the progress counter; does NOT re-write results_summary or checkpoint.
    on_gen_cached: Optional[OnGenCompleteCallback] = None

    # Resume: pre-loaded completed task cache. Key: "{source_doc_id}:{model}:{iteration}"
    # Value: {"doc_id": str, "output_ref": str, "generator": str}
    # Populated by resume_run endpoint before re-launching the executor.
    completed_generation_cache: Dict[str, dict] = field(default_factory=dict)

    # Resume: per-attempt eval coverage cache. Key: generated doc_id.
    # Value: set of (judge model, trial number) tuples already saved for that doc.
    # Built by execute_run_background from existing run_eval_scores rows.
    # Used to backfill only missing eval attempts for cached docs.
    completed_eval_cache: Dict[str, Set[Tuple[str, int]]] = field(default_factory=dict)

    # Callable that returns all current avg scores from the incremental eval store
    # (pre_combine_evals_detailed_incremental). Covers cached/resumed docs whose
    # scores are NOT in result.single_eval_results. Used by pairwise top-N selection
    # so the full, correct score picture is always used, even on resumed runs.
    get_all_eval_scores: Optional[Callable[[], Dict[str, float]]] = None

    @staticmethod
    def model_settings_key(generator: GeneratorType, model: str) -> str:
        generator_name = generator.value if hasattr(generator, "value") else str(generator)
        return f"{generator_name}::{model}"

    def get_model_settings_for_generator(self, generator: GeneratorType, model: str) -> Dict[str, Any]:
        settings_key = self.model_settings_key(generator, model)
        settings = (self.model_settings or {}).get(settings_key)
        if not settings:
            raise ValueError(f"Missing model_settings for {settings_key}")
        return settings

    def _validate_model_settings_entry(
        self,
        *,
        generator: GeneratorType,
        model: str,
        settings: Dict[str, Any],
    ) -> None:
        settings_key = self.model_settings_key(generator, model)
        provider = settings.get("provider")
        base_model = settings.get("model") or (model.split(":", 1)[1] if ":" in model else model)
        temperature = settings.get("temperature")
        max_tokens = settings.get("max_tokens")
        if not provider:
            raise ValueError(f"provider is required for {settings_key} in model_settings")
        if not base_model:
            raise ValueError(f"model name is required for {settings_key} in model_settings")
        if temperature is None:
            raise ValueError(f"temperature is required for {settings_key} in model_settings")
        if max_tokens is None or max_tokens < 1:
            raise ValueError(f"max_tokens must be >= 1 for {settings_key} in model_settings")
        settings["model"] = base_model

    def __post_init__(self):
        """Validate all required fields and conditional requirements."""
        self.general_config = self.general_config or {}
        self.fpf_config = self.fpf_config or {}
        self.gptr_config = self.gptr_config or {}
        self.dr_config = self.dr_config or {}
        self.ma_config = self.ma_config or {}
        self.aiq_config = self.aiq_config or {}
        self.eval_config = self.eval_config or {}
        self.pairwise_config = self.pairwise_config or {}
        self.combine_config = self.combine_config or {}
        self.concurrency_config = self.concurrency_config or {}

        # Validate required numeric fields
        if self.iterations is None or self.iterations < 1:
            raise ValueError("iterations must be >= 1 and is required")
        if self.eval_iterations is None or self.eval_iterations < 1:
            raise ValueError("eval_iterations must be >= 1 and is required")
        if self.generation_concurrency is None or not (1 <= self.generation_concurrency <= 50):
            raise ValueError("generation_concurrency must be 1-50 and is required")
        if self.eval_concurrency is None or not (1 <= self.eval_concurrency <= 50):
            raise ValueError("eval_concurrency must be 1-50 and is required")

        # Validate inputs
        if not self.document_ids:
            raise ValueError("document_ids is required and cannot be empty")
        if not self.document_contents:
            raise ValueError("document_contents is required and cannot be empty")
        for doc_id in self.document_ids:
            if doc_id not in self.document_contents:
                raise ValueError(f"Missing content for document_id: {doc_id}")
            if not self.document_contents[doc_id] or not self.document_contents[doc_id].strip():
                raise ValueError(f"Content for document_id {doc_id} is empty or whitespace")

        # Validate generators
        if not self.generators:
            raise ValueError("generators list is required and cannot be empty")
        unsupported_generators = [
            g.value if hasattr(g, "value") else str(g)
            for g in self.generators
            if g not in (GeneratorType.FPF, GeneratorType.GPTR, GeneratorType.DR, GeneratorType.AIQ)
        ]
        if unsupported_generators:
            raise ValueError(f"Unsupported generators selected: {unsupported_generators}")
        if not self.models:
            raise ValueError("models list is required and cannot be empty")

        # Validate per-model settings
        if not self.model_settings:
            raise ValueError("model_settings is required and cannot be empty")
        generator_model_lists = {
            GeneratorType.FPF: self.fpf_models,
            GeneratorType.GPTR: self.gptr_models,
            GeneratorType.DR: self.dr_models,
            GeneratorType.AIQ: self.aiq_models,
        }
        for generator in self.generators:
            generator_models = generator_model_lists.get(generator)
            if not generator_models:
                raise ValueError(f"{generator.value.upper()} generator enabled but models are missing")
            for model in generator_models:
                settings = self.get_model_settings_for_generator(generator, model)
                self._validate_model_settings_entry(
                    generator=generator,
                    model=model,
                    settings=settings,
                )

        # Validate FPF instructions
        if GeneratorType.FPF in self.generators and not self.instructions:
            raise ValueError(
                "FPF generator requires instructions. "
                "Select instructions from Content Library in preset."
            )

        # Validate evaluation instructions
        if self.enable_single_eval:
            if not self.eval_judge_models:
                raise ValueError("eval_judge_models required when single evaluation enabled")
            if not self.single_eval_instructions:
                raise ValueError(
                    "Single evaluation enabled but no instructions provided. "
                    "Select single_eval_instructions from Content Library in preset."
                )
            if self.eval_retries is None or self.eval_retries < 0 or self.eval_retries > 10:
                raise ValueError("eval_retries must be 0-10 and is required when single evaluation is enabled")
            if self.eval_timeout is None:
                raise ValueError("eval_timeout must be set when single evaluation is enabled")
            if self.eval_max_tokens is None or self.eval_max_tokens < 1:
                raise ValueError("eval_max_tokens must be >= 1 when single evaluation is enabled")
            if self.eval_temperature is None:
                raise ValueError("eval_temperature must be set when single evaluation is enabled")

        if self.enable_pairwise:
            if not self.eval_judge_models:
                raise ValueError("eval_judge_models required when pairwise evaluation enabled")
            if not self.pairwise_eval_instructions:
                raise ValueError(
                    "Pairwise evaluation enabled but no instructions provided. "
                    "Select pairwise_eval_instructions from Content Library in preset."
                )
            if self.eval_retries is None or self.eval_retries < 0 or self.eval_retries > 10:
                raise ValueError("eval_retries must be 0-10 and is required when pairwise evaluation is enabled")
            if self.eval_timeout is None:
                raise ValueError("eval_timeout must be set when pairwise evaluation is enabled")
            if self.eval_max_tokens is None or self.eval_max_tokens < 1:
                raise ValueError("eval_max_tokens must be >= 1 when pairwise evaluation is enabled")
            if self.eval_temperature is None:
                raise ValueError("eval_temperature must be set when pairwise evaluation is enabled")

        if (self.enable_single_eval or self.enable_pairwise) and not self.eval_criteria:
            raise ValueError(
                "Evaluation enabled but no criteria provided. "
                "Select eval_criteria from Content Library in preset."
            )

        # Validate combine configuration
        if self.enable_combine:
            if not self.combine_models:
                raise ValueError(
                    "Combine enabled but no models provided. "
                    "Add at least one combine model in preset."
                )
            if not self.combine_instructions:
                raise ValueError(
                    "Combine enabled but no instructions provided. "
                    "Select combine_instructions from Content Library in preset."
                )
            if not self.combine_strategy:
                raise ValueError("Combine enabled but no strategy provided")
            if self.combine_max_tokens is None:
                raise ValueError("Combine enabled but no combine_max_tokens provided in preset")

        # Validate optional top-N settings
        if self.pairwise_top_n is not None and self.pairwise_top_n < 2:
            raise ValueError("pairwise_top_n must be >= 2 or None")
        if self.post_combine_top_n is not None and self.post_combine_top_n < 2:
            raise ValueError("post_combine_top_n must be >= 2 or None")

        if GeneratorType.FPF in self.generators and not self.fpf_models:
            raise ValueError("FPF generator enabled but fpf_models are missing")
        if GeneratorType.GPTR in self.generators and not self.gptr_models:
            raise ValueError("GPTR generator enabled but gptr_models are missing")
        if GeneratorType.DR in self.generators and not self.dr_models:
            raise ValueError("DR generator enabled but dr_models are missing")
        if GeneratorType.AIQ in self.generators and not self.aiq_models:
            raise ValueError("AIQ generator enabled but aiq_models are missing")

    def get_models_for_generator(self, generator: GeneratorType) -> List[str]:
        """Get the model list for a specific generator without fallbacks."""
        if generator == GeneratorType.FPF:
            if self.fpf_models:
                return self.fpf_models
            raise ValueError("FPF generator enabled but fpf_models are missing")
        elif generator == GeneratorType.GPTR:
            if self.gptr_models:
                return self.gptr_models
            raise ValueError("GPTR generator enabled but gptr_models are missing")
        elif generator == GeneratorType.DR:
            if self.dr_models:
                return self.dr_models
            raise ValueError("DR generator enabled but dr_models are missing")
        elif generator == GeneratorType.AIQ:
            if self.aiq_models:
                return self.aiq_models
            raise ValueError("AIQ generator enabled but aiq_models are missing")
        raise ValueError(f"Unknown generator: {generator}")


@dataclass
class RunProgress:
    """Progress tracking for a run."""
    phase: RunPhase
    total_tasks: int
    completed_tasks: int
    current_task: Optional[str] = None

    @property
    def progress_percent(self) -> float:
        if self.total_tasks == 0:
            return 0.0
        return (self.completed_tasks / self.total_tasks) * 100


@dataclass
class SourceDocResult:
    """Result for a single source document's pipeline execution.

    Each input document runs through its own isolated pipeline:
    Generation → Single Eval → Pairwise → Combine → Output

    Documents never compete across source doc boundaries.
    """
    source_doc_id: str
    source_doc_name: str
    status: RunPhase  # Per-document status

    # Generated variations for this source doc
    generated_docs: List[GeneratedDocument] = field(default_factory=list)

    # Evaluation results (only for this source doc's generated docs)
    single_eval_results: Dict[str, SingleEvalSummary] = field(default_factory=dict)
    pairwise_results: Optional[PairwiseSummary] = None

    # Winner and combined output for this source doc
    winner_doc_id: Optional[str] = None
    combined_doc: Optional[GeneratedDocument] = None  # Legacy: first combined doc
    combined_docs: List[GeneratedDocument] = field(default_factory=list)  # All combined docs

    # Post-combine evaluation for this source doc
    post_combine_eval_results: Optional[PairwiseSummary] = None

    # Timeline events specific to this source doc
    timeline_events: List[Dict[str, Any]] = field(default_factory=list)

    # Per-document errors
    errors: List[str] = field(default_factory=list)
    locked_invariant_failures: List[Dict[str, Any]] = field(default_factory=list)

    # Per-document stats
    duration_seconds: float = 0.0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


@dataclass
class RunResult:
    """Result of a completed run.

    For multi-document runs, results are organized per source document in source_doc_results.
    The legacy flat fields (generated_docs, single_eval_results, etc.) are kept for
    backward compatibility with single-document runs.
    """
    run_id: str
    status: RunPhase

    # === NEW: Per-source-document results ===
    # Each source document has its own isolated pipeline results
    source_doc_results: Dict[str, SourceDocResult] = field(default_factory=dict)

    # === LEGACY: Flat structure for backward compatibility ===
    # These are still populated for single-doc runs and old code paths
    # Generated documents (all, across all source docs for legacy compatibility)
    generated_docs: List[GeneratedDocument] = field(default_factory=list)

    # Evaluation results (legacy: global, not per-source-doc)
    single_eval_results: Optional[Dict[str, SingleEvalSummary]] = None
    pairwise_results: Optional[PairwiseSummary] = None

    # Final output (legacy: single winner across all docs)
    winner_doc_id: Optional[str] = None
    combined_docs: List[GeneratedDocument] = field(default_factory=list)  # All combined docs

    # Post-combine evaluation
    post_combine_eval_results: Optional[PairwiseSummary] = None

    # Stats
    duration_seconds: float = 0.0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    fpf_stats: Optional[Dict[str, Any]] = None  # Live FPF call statistics

    # Errors (run-level)
    errors: List[str] = field(default_factory=list)
    locked_invariant_failures: List[Dict[str, Any]] = field(default_factory=list)


class RunExecutor:
    """
    Executes a full run pipeline.

    Pipeline flow:
    ```
    [Input Docs]
         │
         ▼
    ┌─────────────────────────────────────────┐
    │ GENERATION PHASE                        │
    │ For each (doc, model, iteration):       │
    │   1. Generate document                  │
    │   2. Single-eval IMMEDIATELY ◄────────┐ │  ← STREAMING
    │      (don't wait for other gens)      │ │
    └───────────────────────────────────────┘ │
                      │                       │
                      ▼ (wait for ALL)        │
    ┌─────────────────────────────────────────┐
    │ PAIRWISE PHASE (batch)                  │
    │   1. Collect all single-eval scores     │
    │   2. Filter to Top-N (optional)         │
    │   3. Run pairwise tournament            │
    │   4. Calculate Elo rankings             │
    └─────────────────────────────────────────┘
                      │
                      ▼
    [Winner Document(s)]
    ```
    """

    def __init__(self, run_logger: Optional[logging.Logger] = None):
        self._fpf_adapter = FpfAdapter()
        self._gptr_adapter = GptrAdapter()
        self._dr_adapter = DrAdapter()
        self._aiq_adapter = AiqAdapter()
        self._cancelled = False
        self._paused = False
        self._active_pipelines: list = []  # pipelines created by current _execute_multi_doc()
        self._fpf_stats = FpfStatsTracker()  # Track FPF stats across the run
        # NOTE: Callback is set in execute() with run_id closure, not here

        # Use injected logger or fallback to module logger (legacy/test support)
        self.logger = run_logger or logging.getLogger(__name__)

        # Debug: surface executor creation info
        try:
            self.logger.debug(
                "RunExecutor.__init__ created fpf_adapter=%r gptr_adapter=%r cancelled=%s",
                type(self._fpf_adapter).__name__,
                type(self._gptr_adapter).__name__,
                self._cancelled,
            )
        except Exception:
            self.logger.debug("RunExecutor.__init__ debug log failed", exc_info=True)

    def _stop_requested(self) -> bool:
        """Return True when the run is cooperatively pausing or cancelling."""
        return self._cancelled or self._paused

    async def _acquire_semaphore_or_stop(
        self,
        semaphore: asyncio.Semaphore,
        *,
        poll_interval: float = 0.1,
    ) -> bool:
        """Wait for a semaphore slot, but re-check pause/cancel while queued."""
        while not self._stop_requested():
            try:
                await asyncio.wait_for(semaphore.acquire(), timeout=poll_interval)
                return True
            except asyncio.TimeoutError:
                continue
        return False

    async def _emit_timeline_event(
        self,
        run_id: str,
        phase: str,
        event_type: str,
        description: str,
        model: Optional[str] = None,
        timestamp: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        duration_seconds: Optional[float] = None,
        success: bool = True,
        details: Optional[dict] = None,
    ) -> None:
        """Emit a timeline event to the database for progressive UI updates.

        This appends an event to results_summary.timeline_events so the web GUI
        can show timeline progress during execution, not just at completion.
        """
        event = {
            "phase": phase,
            "event_type": event_type,
            "description": description,
            "model": model,
            "timestamp": (timestamp or datetime.utcnow()).isoformat(),
            "completed_at": completed_at.isoformat() if completed_at else None,
            "duration_seconds": duration_seconds,
            "success": success,
            "details": details,
        }
        # Blob write removed — WRITE_BLOB=False (cutover_flags E2).
        # Normalized write handled by ResultsWriter.write_timeline_event() directly.

    async def execute(self, run_id: str, config: RunConfig, log_writer=None) -> RunResult:
        """
        Execute a full run with per-source-document pipelines.

        Each source document runs through its own isolated pipeline:
        Generation → Single Eval → Pairwise → Combine → Post-Combine Eval

        Documents NEVER compete across source doc boundaries.
        Multiple pipelines run concurrently with shared API semaphore.

        Args:
            run_id: Unique run identifier
            config: Run configuration

        Returns:
            RunResult with all outputs and stats
        """
        # CRITICAL: Set run_id FIRST before any other operations
        self._current_run_id = run_id
        self.config = config  # Store config for access in other methods
        self._log_writer = log_writer  # sidecar DB log writer (optional)
        self.logger.info(f"[STATS] Initializing executor for run {run_id}")
        if self._log_writer:
            await self._log_writer.event("apicostx", "INFO", "executor_init",
                                         f"Initializing executor for run {run_id}")

        # Fix #5: Reset stats for new run
        self._fpf_stats = FpfStatsTracker()
        self.logger.info(f"[STATS] FpfStatsTracker initialized for run_id={run_id}")

        started_at = datetime.utcnow()
        result = RunResult(
            run_id=run_id,
            status=RunPhase.GENERATING,
            generated_docs=[],
            source_doc_results={},
            started_at=started_at,
        )

        # Debug: log a concise run config summary (no secrets)
        try:
            self.logger.debug(
                "Run %s config summary: documents=%d models=%r generators=%r iterations=%d enable_single_eval=%s enable_pairwise=%s save_run_logs=%s",
                run_id,
                len(config.document_ids or []),
                config.models,
                [g.value for g in (config.generators or [])],
                config.iterations,
                config.enable_single_eval,
                config.enable_pairwise,
                config.save_run_logs,
            )
            if self._log_writer:
                await self._log_writer.detail("apicostx", "DEBUG", "Full run config", payload={
                    "run_id": run_id,
                    "documents": len(config.document_ids or []),
                    "models": config.models,
                    "generators": [g.value for g in (config.generators or [])],
                    "iterations": config.iterations,
                    "enable_single_eval": config.enable_single_eval,
                    "enable_pairwise": config.enable_pairwise,
                    "save_run_logs": config.save_run_logs,
                })
        except Exception:
            self.logger.debug("Failed to log run config summary", exc_info=True)

        # Emit run start timeline event
        await self._emit_timeline_event(
            run_id=run_id,
            phase="initialization",
            event_type="start",
            description="Run started",
            timestamp=started_at,
            success=True,
        )

        try:
            # All runs use the per-source-document pipeline architecture.
            # Single-document runs are just a special case with one pipeline.
            await self._execute_multi_doc(run_id, config, result, started_at)

        except Exception as e:
            tb = traceback.format_exc()
            self.logger.error(f"Run {run_id} failed: {e}\n{tb}")
            if self._log_writer:
                await self._log_writer.event("apicostx", "ERROR", "run_failed",
                                             f"Run {run_id} failed: error_type={type(e).__name__}")
                await self._log_writer.detail("apicostx", "ERROR", "Executor traceback",
                                              payload={"traceback": tb})
            result.status = RunPhase.FAILED
            result.errors.append(str(e))
            result.errors.append(tb)
            result.completed_at = datetime.utcnow()

            # Include FPF stats even on failure
            try:
                result.fpf_stats = self._fpf_stats.to_dict()
            except Exception:
                result.fpf_stats = None

            # Emit run failure timeline event
            await self._emit_timeline_event(
                run_id=run_id,
                phase="completion",
                event_type="failed",
                description=f"Run failed: {str(e)[:100]}",
                timestamp=result.completed_at,
                success=False,
                details={"error": str(e)},
            )

        return result

    async def _execute_multi_doc(
        self,
        run_id: str,
        config: RunConfig,
        result: RunResult,
        started_at: datetime,
    ) -> None:
        """
        Execute run with per-source-document pipelines.

        Each source document runs through its own isolated pipeline.
        Pipelines run concurrently with shared API semaphore.
        """
        from .source_doc_pipeline import SourceDocPipeline

        self.logger.info(f"Run {run_id}: Starting multi-doc execution with {len(config.document_ids)} documents")
        if self._log_writer:
            await self._log_writer.event("apicostx", "INFO", "generation_start",
                                         f"Run {run_id}: multi-doc execution starting, docs={len(config.document_ids)}")

        # Create shared semaphores
        # API semaphore limits concurrent API calls across all pipelines
        api_semaphore = asyncio.Semaphore(config.generation_concurrency)
        # Pipeline semaphore limits how many pipelines are active simultaneously
        pipeline_semaphore = asyncio.Semaphore(config.max_concurrent_pipelines)

        # Create a pipeline for each source document
        pipelines = []
        for doc_id in config.document_ids:
            # Get human-readable name from document_names or fall back to doc_id
            doc_name = config.document_names.get(doc_id, doc_id)

            pipeline = SourceDocPipeline(
                source_doc_id=doc_id,
                source_doc_name=doc_name,
                content=config.document_contents[doc_id],
                config=config,
                run_id=run_id,
                shared_semaphore=api_semaphore,
                stats_tracker=self._fpf_stats,
                fpf_adapter=self._fpf_adapter,
                gptr_adapter=self._gptr_adapter,
                dr_adapter=self._dr_adapter,
                aiq_adapter=self._aiq_adapter,
                logger=self.logger,
                run_store=getattr(self, '_run_store', None),
                on_timeline_event=self._on_pipeline_timeline_event,
                log_writer=self._log_writer,
            )
            pipelines.append(pipeline)

        self._active_pipelines = pipelines  # expose for pause()

        async def run_pipeline_with_limit(pipeline: SourceDocPipeline):
            """Run a single pipeline with concurrency limiting."""
            acquired = await self._acquire_semaphore_or_stop(pipeline_semaphore)
            if not acquired:
                if self._cancelled:
                    pipeline.cancel()
                if self._paused:
                    pipeline.pause()
                stopped_at = datetime.utcnow()
                return SourceDocResult(
                    source_doc_id=pipeline.source_doc_id,
                    source_doc_name=config.document_names.get(
                        pipeline.source_doc_id,
                        pipeline.source_doc_id,
                    ),
                    status=RunPhase.CANCELLED,
                    started_at=stopped_at,
                    completed_at=stopped_at,
                    duration_seconds=0.0,
                )

            try:
                if self._cancelled:
                    pipeline.cancel()
                if self._paused:
                    pipeline.pause()
                return await pipeline.run()
            finally:
                pipeline_semaphore.release()

        # Run all pipelines concurrently
        pipeline_results = await asyncio.gather(
            *[run_pipeline_with_limit(p) for p in pipelines],
            return_exceptions=True,
        )

        # Collect results from all pipelines
        all_completed = True
        any_success = False
        any_completed_with_errors = False

        for doc_id, pipe_result in zip(config.document_ids, pipeline_results):
            if isinstance(pipe_result, Exception):
                # Pipeline threw an exception
                self.logger.error(f"Run {run_id}: Pipeline for {doc_id} failed with exception: {pipe_result}")
                doc_name = config.document_names.get(doc_id, doc_id)
                result.source_doc_results[doc_id] = SourceDocResult(
                    source_doc_id=doc_id,
                    source_doc_name=doc_name,
                    status=RunPhase.FAILED,
                    errors=[str(pipe_result)],
                )
                all_completed = False
            else:
                # Store pipeline result
                result.source_doc_results[doc_id] = pipe_result
                if pipe_result.errors:
                    _doc_prefix = pipe_result.source_doc_name or doc_id
                    result.errors.extend(
                        f"{_doc_prefix}: {error}"
                        for error in pipe_result.errors
                    )
                if pipe_result.locked_invariant_failures:
                    result.locked_invariant_failures.extend(pipe_result.locked_invariant_failures)

                # Track success/failure
                if pipe_result.status in (RunPhase.COMPLETED, RunPhase.COMPLETED_WITH_ERRORS):
                    any_success = True
                if pipe_result.status == RunPhase.COMPLETED_WITH_ERRORS:
                    any_completed_with_errors = True
                elif pipe_result.status not in (
                    RunPhase.COMPLETED,
                    RunPhase.CANCELLED,
                ):
                    all_completed = False

                # Aggregate generated docs into legacy flat list for backward compat
                result.generated_docs.extend(pipe_result.generated_docs)

                # Aggregate per-source-doc results into top-level RunResult fields
                # so that both serializers (presets.py, execution.py) see populated data.
                if pipe_result.single_eval_results:
                    if result.single_eval_results is None:
                        result.single_eval_results = {}
                    result.single_eval_results.update(pipe_result.single_eval_results)

                if pipe_result.pairwise_results and result.pairwise_results is None:
                    result.pairwise_results = pipe_result.pairwise_results

                if pipe_result.winner_doc_id and result.winner_doc_id is None:
                    result.winner_doc_id = pipe_result.winner_doc_id

                if pipe_result.combined_docs:
                    result.combined_docs.extend(pipe_result.combined_docs)
                elif pipe_result.combined_doc:
                    result.combined_docs.append(pipe_result.combined_doc)

                if pipe_result.post_combine_eval_results and result.post_combine_eval_results is None:
                    result.post_combine_eval_results = pipe_result.post_combine_eval_results

        self.logger.info(
            f"Run {run_id}: Aggregated from {len(result.source_doc_results)} source docs: "
            f"single_eval={len(result.single_eval_results or {})} docs, "
            f"pairwise={'yes' if result.pairwise_results else 'no'}, "
            f"winner={result.winner_doc_id}, "
            f"combined_docs={len(result.combined_docs)}"
        )
        if self._log_writer:
            await self._log_writer.event("apicostx", "INFO", "generation_complete",
                                         f"Run {run_id}: generation phase complete, docs_completed={len(result.source_doc_results)}")

        # Determine overall run status
        if self._cancelled:
            result.status = RunPhase.CANCELLED
        elif self._paused:
            # Drain-and-hold pause: pipelines drained, mark as CANCELLED so background
            # task can detect _paused=True and set DB status to PAUSED.
            result.status = RunPhase.CANCELLED
        elif all_completed and any_success:
            result.status = (
                RunPhase.COMPLETED_WITH_ERRORS
                if any_completed_with_errors or result.errors
                else RunPhase.COMPLETED
            )
        elif any_success:
            result.status = RunPhase.COMPLETED_WITH_ERRORS
        else:
            result.status = RunPhase.FAILED

        # Finalize result
        result.completed_at = datetime.utcnow()
        result.duration_seconds = (result.completed_at - started_at).total_seconds()

        # Include FPF stats
        try:
            result.fpf_stats = self._fpf_stats.to_dict()
        except Exception:
            result.fpf_stats = None

        # Emit run completion timeline event
        _completed_doc_count = len(
            [
                r
                for r in result.source_doc_results.values()
                if r.status in (RunPhase.COMPLETED, RunPhase.COMPLETED_WITH_ERRORS)
            ]
        )
        _completion_description = (
            f"Run completed with errors: {_completed_doc_count}/{len(config.document_ids)} documents finished"
            if result.status == RunPhase.COMPLETED_WITH_ERRORS
            else f"Run completed: {_completed_doc_count}/{len(config.document_ids)} documents succeeded"
        )
        await self._emit_timeline_event(
            run_id=run_id,
            phase="completion",
            event_type="complete",
            description=_completion_description,
            timestamp=result.completed_at,
            duration_seconds=result.duration_seconds,
            success=result.status in (RunPhase.COMPLETED, RunPhase.COMPLETED_WITH_ERRORS),
        )

        # =========================================================================
        # Write Output - Push winning documents to configured destination
        # =========================================================================
        if result.status in (RunPhase.COMPLETED, RunPhase.COMPLETED_WITH_ERRORS):
            if config.output_destination != "none":
                await self._write_outputs(run_id, config, result)

        self.logger.info(
            f"Run {run_id}: Multi-doc execution completed | "
            f"status={result.status.value} "
            f"docs={len(result.generated_docs)}"
        )

    async def _on_pipeline_timeline_event(self, run_id: str, event: dict) -> None:
        """Handle timeline events from SourceDocPipeline instances."""
        # Persist only the event families that are NOT rebuilt reliably from the
        # completion snapshot path. Generation/pairwise/combine successes are
        # still written at completion time; single-eval and degraded/skip/error
        # rows need incremental writes so the per-source-doc timeline stays
        # truthful during and after execution.
        try:
            source_doc_id = event.get("source_doc_id")
            event_type = event.get("event_type")
            phase = event.get("phase")
            persist_incrementally = event_type in {
                "single_eval",
                "single_eval_degraded",
                "pairwise_skipped",
                "pairwise_failed",
                "pairwise_degraded",
                "combine_skipped",
                "combine_failed",
                "post_combine_skipped",
                "post_combine_failed",
                "post_combine_degraded",
            }
            if source_doc_id and persist_incrementally:
                occurred_at = (
                    datetime.fromisoformat(event["completed_at"])
                    if event.get("completed_at")
                    else datetime.fromisoformat(event["timestamp"])
                    if event.get("timestamp")
                    else datetime.utcnow()
                )
                details = {
                    **(event.get("details") or {}),
                    "source_doc_id": source_doc_id,
                    "source_doc_name": event.get("source_doc_name"),
                }
                from app.infra.db.repositories.run_results import RunResultsRepository

                async with get_user_session_by_uuid(self.config.user_uuid) as session:
                    results_repo = RunResultsRepository(session)
                    await results_repo.insert_timeline_event(
                        run_id=run_id,
                        phase=phase or "unknown",
                        event_type=event_type or "unknown",
                        source_doc_id=source_doc_id,
                        doc_id=details.get("doc_id"),
                        description=event.get("description"),
                        model=event.get("model"),
                        success=event.get("success", True),
                        duration_seconds=event.get("duration_seconds"),
                        details_json=json.dumps(details) if details else None,
                        occurred_at=occurred_at,
                    )
                    await session.commit()
        except Exception as e:
            self.logger.warning(f"Failed to append source-doc timeline event for run {run_id}: {e}")

        await self._emit_timeline_event(
            run_id=run_id,
            phase=event.get("phase", ""),
            event_type=event.get("event_type", ""),
            description=event.get("description", ""),
            model=event.get("model"),
            timestamp=datetime.fromisoformat(event["timestamp"]) if event.get("timestamp") else None,
            completed_at=datetime.fromisoformat(event["completed_at"]) if event.get("completed_at") else None,
            duration_seconds=event.get("duration_seconds"),
            success=event.get("success", True),
            details={
                **(event.get("details") or {}),
                "source_doc_id": event.get("source_doc_id"),
                "source_doc_name": event.get("source_doc_name"),
            },
        )

    async def _write_outputs(
        self,
        run_id: str,
        config: RunConfig,
        result: RunResult,
    ) -> None:
        """
        Write winning documents to configured destinations.

        For each source document that has a winner, writes to:
        - Content Library (always for library/github destinations)
        - GitHub repository (if github destination)

        Args:
            run_id: The run ID
            config: Run configuration with output settings
            result: Run result with per-source-doc winners
        """
        self.logger.info(f"Run {run_id}: Writing outputs to {config.output_destination}")

        try:
            async with get_user_session_by_uuid(config.user_uuid) as db:
                output_writer = OutputWriter(db, config.user_uuid)

                written_count = 0
                error_count = 0

                for source_doc_id, source_doc_result in result.source_doc_results.items():
                    # Skip failed source docs
                    if source_doc_result.status not in (RunPhase.COMPLETED, RunPhase.COMPLETED_WITH_ERRORS):
                        self.logger.debug(
                            f"Run {run_id}: Skipping output for {source_doc_result.source_doc_name} "
                            f"(status={source_doc_result.status.value})"
                        )
                        continue

                    # Find winner content
                    winner_content: Optional[str] = None
                    winner_doc_id: Optional[str] = None
                    winner_model: str = "unknown"

                    # Prefer combined doc if exists
                    if source_doc_result.combined_docs:
                        combined_doc = source_doc_result.combined_docs[0]
                        winner_content = combined_doc.content
                        winner_doc_id = combined_doc.doc_id
                        winner_model = combined_doc.model
                    elif source_doc_result.combined_doc:
                        winner_content = source_doc_result.combined_doc.content
                        winner_doc_id = source_doc_result.combined_doc.doc_id
                        winner_model = source_doc_result.combined_doc.model
                    # Otherwise use pairwise winner
                    elif source_doc_result.winner_doc_id:
                        winner_doc_id = source_doc_result.winner_doc_id
                        # Find the generated doc with this ID
                        for gen_doc in source_doc_result.generated_docs:
                            if gen_doc.doc_id == winner_doc_id:
                                winner_content = gen_doc.content
                                winner_model = gen_doc.model
                                break

                    # Generation-only mode: no eval phases enabled, write ALL generated docs
                    if not winner_content or not winner_doc_id:
                        generation_only = (
                            not config.enable_single_eval
                            and not config.enable_pairwise
                            and not config.enable_combine
                        )
                        if generation_only and source_doc_result.generated_docs:
                            self.logger.info(
                                f"Run {run_id}: Generation-only mode for {source_doc_result.source_doc_name}, "
                                f"writing all {len(source_doc_result.generated_docs)} generated docs"
                            )
                            for gen_doc in source_doc_result.generated_docs:
                                # Ensure unique filenames by including model if template lacks it
                                gen_template = config.output_filename_template
                                if "{winner_model}" not in gen_template:
                                    gen_template = gen_template.replace("_winner", "_{winner_model}")
                                    if gen_template == config.output_filename_template:
                                        gen_template += "_{winner_model}"
                                _gen_content = gen_doc.content
                                if config.prepend_source_first_line_frontmatter and source_doc_id in config.document_contents:
                                    _gen_content = _prepend_frontmatter(_gen_content, config.document_contents[source_doc_id])
                                write_result = await output_writer.write_winner(
                                    content=_gen_content,
                                    output_destination=config.output_destination,
                                    filename_template=gen_template,
                                    run_id=run_id,
                                    winner_doc_id=gen_doc.doc_id,
                                    source_doc_name=source_doc_result.source_doc_name,
                                    winner_model=gen_doc.model,
                                    github_connection_id=config.github_connection_id,
                                    github_output_path=config.github_output_path,
                                    github_commit_message=f"APICostX Run {run_id[:8]}: {gen_doc.model} output for {source_doc_result.source_doc_name}",
                                    preset_name=config.preset_name,
                                    source_relative_path=config.document_relative_paths.get(source_doc_id),
                                )
                                if write_result.success:
                                    written_count += 1
                                    self.logger.info(
                                        f"Run {run_id}: Wrote generated doc {gen_doc.doc_id} for {source_doc_result.source_doc_name} "
                                        f"(model={gen_doc.model}, content_id={write_result.content_id}, github_url={write_result.github_url})"
                                    )
                                else:
                                    error_count += 1
                                    self.logger.error(
                                        f"Run {run_id}: Failed to write generated doc for {source_doc_result.source_doc_name}: "
                                        f"{write_result.error}"
                                    )
                                    result.errors.append(
                                        f"Output write failed for {source_doc_result.source_doc_name} ({gen_doc.model}): {write_result.error}"
                                    )
                            continue
                        else:
                            self.logger.warning(
                                f"Run {run_id}: No winner content found for {source_doc_result.source_doc_name}"
                            )
                            continue

                    # Write winner to destination
                    if config.prepend_source_first_line_frontmatter and source_doc_id in config.document_contents:
                        winner_content = _prepend_frontmatter(winner_content, config.document_contents[source_doc_id])
                    write_result = await output_writer.write_winner(
                        content=winner_content,
                        output_destination=config.output_destination,
                        filename_template=config.output_filename_template,
                        run_id=run_id,
                        winner_doc_id=winner_doc_id,
                        source_doc_name=source_doc_result.source_doc_name,
                        winner_model=winner_model,
                        github_connection_id=config.github_connection_id,
                        github_output_path=config.github_output_path,
                        github_commit_message=f"APICostX Run {run_id[:8]}: Output for {source_doc_result.source_doc_name}",
                        preset_name=config.preset_name,
                        source_relative_path=config.document_relative_paths.get(source_doc_id),
                    )

                    if write_result.success:
                        written_count += 1
                        self.logger.info(
                            f"Run {run_id}: Wrote output for {source_doc_result.source_doc_name} "
                            f"(content_id={write_result.content_id}, github_url={write_result.github_url})"
                        )
                    else:
                        error_count += 1
                        self.logger.error(
                            f"Run {run_id}: Failed to write output for {source_doc_result.source_doc_name}: "
                            f"{write_result.error}"
                        )
                        result.errors.append(
                            f"Output write failed for {source_doc_result.source_doc_name}: {write_result.error}"
                        )

                self.logger.info(
                    f"Run {run_id}: Output writing completed - "
                    f"{written_count} written, {error_count} errors"
                )

        except Exception as e:
            self.logger.exception(f"Run {run_id}: Output writing failed: {e}")
            result.errors.append(f"Output writing failed: {str(e)}")

    def cancel(self) -> None:
        """Cancel the running execution."""
        self._cancelled = True
        for pipeline in self._active_pipelines:
            try:
                pipeline.cancel()
            except Exception:
                pass

    def pause(self) -> None:
        """
        Signal all active pipelines to pause (drain-and-hold).

        In-flight generation tasks will finish; tasks waiting on the
        semaphore will see the flag and return without doing work.
        The executor status and DB run status are updated by the caller
        (execution.py pause endpoint).
        """
        self._paused = True
        for pipeline in self._active_pipelines:
            try:
                pipeline.pause()
            except Exception:
                pass


# Note: RunExecutor instances are created per-run by callers to ensure
# cancellation and internal state do not leak between runs.


def get_executor() -> RunExecutor:
    """Compatibility shim: return a fresh RunExecutor instance.

    Old code imported `get_executor()` expecting a singleton; we return
    a new instance to preserve per-run isolation while keeping imports
    working for older modules.
    """
    logging.getLogger(__name__).debug("get_executor() called - returning new RunExecutor instance")
    return RunExecutor()
