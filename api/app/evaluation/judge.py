"""
LLM Judge for document evaluation.

Uses FPF adapter to call LLMs for single-doc and pairwise evaluation.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

from ..adapters.fpf.adapter import FpfAdapter
from ..adapters.base import GenerationConfig
from ..services.rate_limiter import RateLimitedRequest
from .criteria import CriteriaManager, format_criteria_for_prompt
from .models import (
    CriterionScore,
    EvaluationCriterion,
    PairwiseResult,
    SingleEvalResult,
)

logger = logging.getLogger(__name__)

_SINGLE_EVAL_DOCUMENT_TOKENS = ("{document}", "{content}")
_PAIRWISE_DOC_A_TOKENS = ("{doc_a}", "{document_a}")
_PAIRWISE_DOC_B_TOKENS = ("{doc_b}", "{document_b}")
_CRITERIA_TOKEN = "{criteria}"


def _replace_tokens(template: str, tokens: tuple[str, ...], value: str) -> tuple[str, bool]:
    """Replace all matching tokens in a prompt template."""
    replaced = False
    result = template
    for token in tokens:
        if token in result:
            result = result.replace(token, value)
            replaced = True
    return result, replaced


def _append_labeled_block(parts: list[str], label: str, body: str) -> None:
    """Append a normalized prompt block when the authored prompt omitted payload markers."""
    parts.append(f"**{label}:**")
    parts.append(body)
    parts.append("")


def _build_single_eval_prompt(prompt_template: str, content: str, criteria_text: str) -> str:
    """
    Build a single-eval prompt without depending on authored placeholder syntax.

    Legacy placeholders remain supported for compatibility, but the runtime payload
    is appended structurally when those placeholders are absent.
    """
    prompt, has_document = _replace_tokens(prompt_template, _SINGLE_EVAL_DOCUMENT_TOKENS, content)
    prompt, has_criteria = _replace_tokens(prompt, (_CRITERIA_TOKEN,), criteria_text)

    if has_document and has_criteria:
        return prompt

    parts = [prompt.rstrip(), ""]
    if not has_criteria:
        _append_labeled_block(parts, "EVALUATION CRITERIA", criteria_text)
    if not has_document:
        _append_labeled_block(parts, "DOCUMENT TO EVALUATE", content)
    return "\n".join(parts).strip()


def _build_pairwise_prompt(
    prompt_template: str,
    content_1: str,
    content_2: str,
    criteria_text: str,
) -> str:
    """
    Build a pairwise prompt without depending on authored placeholder syntax.

    Legacy placeholders remain supported for compatibility, but the runtime payload
    is appended structurally when those placeholders are absent.
    """
    prompt, has_doc_a = _replace_tokens(prompt_template, _PAIRWISE_DOC_A_TOKENS, content_1)
    prompt, has_doc_b = _replace_tokens(prompt, _PAIRWISE_DOC_B_TOKENS, content_2)
    prompt, has_criteria = _replace_tokens(prompt, (_CRITERIA_TOKEN,), criteria_text)

    if has_doc_a and has_doc_b and has_criteria:
        return prompt

    parts = [prompt.rstrip(), ""]
    if not has_criteria:
        _append_labeled_block(parts, "EVALUATION CRITERIA", criteria_text)
    if not has_doc_a:
        _append_labeled_block(parts, "DOCUMENT A", content_1)
    if not has_doc_b:
        _append_labeled_block(parts, "DOCUMENT B", content_2)
    return "\n".join(parts).strip()


def _extract_thinking_tokens(metadata: Optional[Dict[str, Any]]) -> Optional[int]:
    if not isinstance(metadata, dict):
        return None

    def _safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except Exception:
            return 0

    total = 0
    provider_specific = metadata.get("tokens_provider_specific")
    if isinstance(provider_specific, dict):
        total += _safe_int(provider_specific.get("reasoning_tokens"))
        total += _safe_int(provider_specific.get("thoughts_tokens"))

    total += _safe_int(metadata.get("reasoning_tokens"))
    total += _safe_int(metadata.get("thoughts_tokens"))

    usage_events = metadata.get("usage_events")
    if isinstance(usage_events, list):
        for evt in usage_events:
            if not isinstance(evt, dict):
                continue
            ps = evt.get("tokens_provider_specific")
            if isinstance(ps, dict):
                total += _safe_int(ps.get("reasoning_tokens"))
                total += _safe_int(ps.get("thoughts_tokens"))
            total += _safe_int(evt.get("reasoning_tokens"))
            total += _safe_int(evt.get("thoughts_tokens"))

    return total if total > 0 else None

# Official criterion names — any name returned by a judge not in this set is discarded
# NOTE: Criteria names are validated dynamically at runtime against the
# criteria defined in the run's criteria document. See evaluate_single().


@dataclass
class FpfStatsTracker:
    """Tracks live FPF call statistics."""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    retries: int = 0
    current_phase: Optional[str] = None
    current_call: Optional[str] = None
    last_error: Optional[str] = None
    _on_update: Optional[Callable[["FpfStatsTracker"], None]] = field(default=None, repr=False)

    def record_call_start(self, phase: str, description: str):
        """Record the start of an FPF call."""
        self.current_phase = phase
        self.current_call = description
        self._notify()

    def record_success(self):
        """Record a successful FPF call."""
        self.total_calls += 1
        self.successful_calls += 1
        self.current_call = None
        self.last_error = None  # Clear previous errors on success
        self._notify()

    def record_failure(self, error: str):
        """Record a failed FPF call."""
        self.total_calls += 1
        self.failed_calls += 1
        self.last_error = error
        self.current_call = None
        self._notify()

    def record_retry(self, attempt: int, error: str):
        """Record a retry attempt."""
        self.retries += 1
        self.last_error = f"Retry {attempt}: {error}"
        self._notify()

    def _notify(self):
        """Notify listener of stats update."""
        if self._on_update:
            try:
                self._on_update(self)
            except Exception as e:
                logger.error(f"FPF stats update callback failed: {e}", exc_info=True)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "total_calls": self.total_calls,
            "successful_calls": self.successful_calls,
            "failed_calls": self.failed_calls,
            "retries": self.retries,
            "current_phase": self.current_phase,
            "current_call": self.current_call,
            "last_error": self.last_error,
        }


@dataclass
class JudgeConfig:
    """
    Configuration for LLM judge.

    Note: timeout_seconds and retries are set here as reasonable defaults.
    In future, these should be loaded from evaluation config or preset.
    """

    model: str = ""  # REQUIRED - must be set by caller
    temperature: float = 0.0
    max_tokens: int = 16384
    thinking_budget_tokens: Optional[int] = None  # Thinking/reasoning budget (None = provider default)
    timeout_seconds: int = 600  # Increased from 120s to handle slow models
    retries: int = 3  # Increased from 2 for better resilience
    # NOTE: enable_grounding removed - FPF always uses grounding, non-configurable
    key_mode: str = 'byok'  # 'byok' = user keys, 'system' = platform keys


def _parse_json_response(text: str) -> Dict[str, Any]:
    """Parse JSON from LLM response."""
    text = text.strip()

    # Remove markdown code blocks if present
    if text.startswith("```"):
        lines = text.split("\n")
        start = 1 if lines[0].startswith("```") else 0
        end = len(lines)
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip() == "```":
                end = i
                break
        text = "\n".join(lines[start:end]).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in text
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        return json.loads(match.group(0))

    raise ValueError(f"No valid JSON found in response: {text[:200]}...")


class Judge:
    """
    LLM-based document judge using FPF adapter.

    Supports both single-document graded evaluation and pairwise comparison.

    IMPORTANT: No default prompts are provided. Custom instructions MUST be
    supplied from the preset's Content Library. This is by design to ensure
    all evaluation behavior is explicitly configured.
    """

    def __init__(
        self,
        config: Optional[JudgeConfig] = None,
        criteria_manager: Optional[CriteriaManager] = None,
        fpf_adapter: Optional[FpfAdapter] = None,
        custom_prompt: Optional[str] = None,
        stats_tracker: Optional[FpfStatsTracker] = None,
        user_uuid: Optional[str] = None,
        run_id: Optional[str] = None,
    ):
        """
        Initialize the judge.

        Args:
            config: Judge configuration
            criteria_manager: Criteria manager instance
            fpf_adapter: FPF adapter instance (created if not provided)
            custom_prompt: Custom evaluation prompt from Content Library (REQUIRED for eval)
            stats_tracker: Optional stats tracker for live FPF call monitoring
            run_id: Run UUID for log correlation
        """
        self.config = config or JudgeConfig()
        self.criteria = criteria_manager or CriteriaManager()
        self._fpf = fpf_adapter
        self.custom_prompt = custom_prompt
        self.stats = stats_tracker  # Use the tracker as-is, don't create fallback
        self.user_uuid = user_uuid
        self.run_id = run_id

    @property
    def fpf(self) -> FpfAdapter:
        """Get or create FPF adapter."""
        if self._fpf is None:
            self._fpf = FpfAdapter()
        return self._fpf

    async def evaluate_single(
        self,
        doc_id: str,
        content: str,
        trial: int = 1,
        criteria: Optional[List[EvaluationCriterion]] = None,
        custom_prompt: Optional[str] = None,
    ) -> SingleEvalResult:
        """
        Perform single-document graded evaluation.

        Args:
            doc_id: Document identifier
            content: Document content to evaluate
            trial: Trial number for multi-iteration runs
            criteria: Optional custom criteria (uses manager's if not provided)
            custom_prompt: Custom evaluation prompt (overrides instance prompt)

        Returns:
            SingleEvalResult with scores for each criterion

        Raises:
            RuntimeError: If evaluation fails after retries or no prompt provided
        """
        from datetime import datetime

        # Determine prompt to use: parameter > instance > ERROR
        prompt_template = custom_prompt or self.custom_prompt
        if not prompt_template:
            raise RuntimeError(
                "No evaluation prompt provided. Single eval requires custom_instructions "
                "from the preset's Content Library. Configure single_eval_instructions_id "
                "in your preset."
            )

        started_at = datetime.utcnow()
        crit_list = criteria or self.criteria.criteria
        criteria_text = format_criteria_for_prompt(crit_list)

        try:
            prompt = _build_single_eval_prompt(prompt_template, content, criteria_text)
        except Exception as e:
            logger.warning(f"Error building single eval prompt: {e}")
            prompt = prompt_template

        last_error = None
        raw_response = None

        for attempt in range(self.config.retries + 1):
            try:
                # Track call start
                if self.stats:
                    self.stats.record_call_start("single_eval", f"Evaluating {doc_id} (attempt {attempt + 1})")

                # Extract provider from model name (format: "provider:model_name")
                if ":" in self.config.model:
                    provider, base_model = self.config.model.split(":", 1)
                else:
                    raise RuntimeError(f"Judge model must include provider prefix: {self.config.model}")

                if self.user_uuid is None:
                    raise RuntimeError("user_uuid is required for evaluation calls")

                # Build config for FPF adapter
                eval_task_id = f"{doc_id}.single_eval.{trial}.{self.config.model}.{uuid4().hex[:6]}"
                gen_config = GenerationConfig(
                    provider=provider,
                    model=base_model,
                    extra={
                        "max_completion_tokens": self.config.max_tokens,
                        "temperature": self.config.temperature,
                        "thinking_budget_tokens": self.config.thinking_budget_tokens,
                        "json_output": True,  # Eval responses are JSON, skip 3KB minimum check
                        "timeout": self.config.timeout_seconds,
                        "task_id": eval_task_id,
                        "run_id": self.run_id,  # For log correlation
                        "phase": "fpf.evaluate",  # For evaluation log categorization
                        "document_id": doc_id,  # For per-document log context
                        "key_mode": self.config.key_mode,
                    },
                )

                # INSTRUMENTATION: Log before FPF dispatch
                logger.info(
                    "[EVAL-DISPATCH] single_eval task_id=%s provider=%s model=%s max_completion_tokens=%s "
                    "temperature=%s thinking_budget_tokens=%s timeout=%ss hard_timeout=%ss key_mode=%s json_output=%s retries=%s",
                    eval_task_id,
                    provider,
                    base_model,
                    self.config.max_tokens,
                    self.config.temperature,
                    self.config.thinking_budget_tokens,
                    self.config.timeout_seconds,
                    self.config.timeout_seconds + 30,
                    self.config.key_mode,
                    True,
                    self.config.retries,
                )

                # Call FPF for evaluation with hard timeout to prevent indefinite hangs
                # Apply provider-level rate limiting before making API call
                # NOTE: FPF has its own retry logic for API errors (429, 500s) - don't retry those here
                try:
                    async with RateLimitedRequest(provider):
                        result = await asyncio.wait_for(
                            self.fpf.generate(
                                query=prompt,
                                config=gen_config,
                                user_uuid=self.user_uuid,
                            ),
                            timeout=float(self.config.timeout_seconds + 30),  # Add buffer over FPF's internal timeout
                        )
                except asyncio.TimeoutError:
                    logger.error(f"[EVAL-DISPATCH] HARD TIMEOUT: FPF single_eval call for {eval_task_id} exceeded {self.config.timeout_seconds + 30}s")
                    # Timeout is fatal - FPF already timed out internally, don't retry
                    raise RuntimeError(f"Single eval call timed out after {self.config.timeout_seconds + 30}s for {eval_task_id}")
                except RuntimeError as fpf_err:
                    # RuntimeError from FPF means API failure after FPF's own retries - don't retry again
                    logger.error(f"[EVAL-DISPATCH] FPF API error (not retriable): {fpf_err}")
                    if self.stats:
                        self.stats.record_failure(str(fpf_err))
                    raise

                logger.info(f"[EVAL-DISPATCH] FPF single_eval completed for {eval_task_id}")

                raw_response = result.content

                # Parse JSON response - THESE errors ARE retriable (malformed LLM output)
                try:
                    data = _parse_json_response(raw_response)

                    # Extract scores
                    evaluations = data.get("evaluations", [])
                    if not evaluations:
                        raise ValueError("No evaluations in response")

                    # Build allowed set from the criteria actually defined for this run
                    allowed_names = {c.name for c in crit_list}

                    scores = []
                    for eval_item in evaluations:
                        criterion = eval_item.get("criterion", "")
                        if criterion not in allowed_names:
                            logger.warning("[EVAL-PARSE] Discarding unrecognized criterion from judge response (allowed_count=%d)", len(allowed_names))
                            continue
                        scores.append(CriterionScore(
                            criterion=criterion,
                            score=int(eval_item["score"]),
                            reason=eval_item.get("reason", ""),
                        ))
                except (ValueError, KeyError, TypeError, json.JSONDecodeError) as parse_err:
                    # Parse/validation errors - these ARE retriable at eval level
                    last_error = parse_err
                    if attempt < self.config.retries:
                        if self.stats:
                            self.stats.record_retry(attempt + 1, f"Parse error: {parse_err}")
                        logger.warning(f"Single eval attempt {attempt + 1} parse error for {doc_id}: {parse_err}")
                        continue  # Retry with a fresh FPF call
                    else:
                        if self.stats:
                            self.stats.record_failure(f"Parse error: {parse_err}")
                        raise RuntimeError(f"Single evaluation failed after {self.config.retries + 1} attempts: {parse_err}")

                # Track success
                if self.stats:
                    self.stats.record_success()

                completed_at = datetime.utcnow()
                return SingleEvalResult(
                    doc_id=doc_id,
                    model=self.config.model,
                    trial=trial,
                    scores=scores,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_seconds=(completed_at - started_at).total_seconds(),
                    raw_response=raw_response,
                    tokens_in=result.input_tokens,
                    tokens_out=result.output_tokens,
                    tokens_thinking=_extract_thinking_tokens(getattr(result, "metadata", None)),
                )

            except RuntimeError:
                # Already handled above - propagate up
                raise
            except Exception as e:
                # Unexpected errors - log and propagate
                logger.error(f"Unexpected error in single_eval for {doc_id}: {e}")
                if self.stats:
                    self.stats.record_failure(str(e))
                raise

        raise RuntimeError(
            f"Single evaluation failed after {self.config.retries + 1} attempts: {last_error}"
        )

    async def evaluate_pairwise(
        self,
        doc_id_1: str,
        content_1: str,
        doc_id_2: str,
        content_2: str,
        trial: int = 1,
        criteria: Optional[List[EvaluationCriterion]] = None,
        custom_prompt: Optional[str] = None,
    ) -> PairwiseResult:
        """
        Perform pairwise comparison between two documents.

        Documents are anonymized as A and B to prevent bias.

        Args:
            doc_id_1: First document identifier
            content_1: First document content
            doc_id_2: Second document identifier
            content_2: Second document content
            trial: Trial number for multi-iteration runs
            criteria: Optional custom criteria
            custom_prompt: Custom pairwise prompt (overrides instance prompt)

        Returns:
            PairwiseResult with winner and reason

        Raises:
            RuntimeError: If comparison fails after retries or no prompt provided
        """
        from datetime import datetime

        # Determine prompt to use: parameter > instance > ERROR
        prompt_template = custom_prompt or self.custom_prompt
        if not prompt_template:
            raise RuntimeError(
                "No pairwise prompt provided. Pairwise eval requires custom_instructions "
                "from the preset's Content Library. Configure pairwise_eval_instructions_id "
                "in your preset."
            )

        started_at = datetime.utcnow()
        crit_list = criteria or self.criteria.criteria
        criteria_text = format_criteria_for_prompt(crit_list)

        try:
            prompt = _build_pairwise_prompt(prompt_template, content_1, content_2, criteria_text)
        except Exception as e:
            logger.warning(f"Error building pairwise prompt: {e}")
            prompt = prompt_template

        last_error = None
        raw_response = None

        for attempt in range(self.config.retries + 1):
            try:
                # Track call start
                if self.stats:
                    self.stats.record_call_start("pairwise_eval", f"Comparing {doc_id_1} vs {doc_id_2} (attempt {attempt + 1})")

                # Extract provider from model name (format: "provider:model_name")
                if ":" in self.config.model:
                    provider, base_model = self.config.model.split(":", 1)
                else:
                    raise RuntimeError(f"Judge model must include provider prefix: {self.config.model}")

                if self.user_uuid is None:
                    raise RuntimeError("user_uuid is required for evaluation calls")

                # Build config for FPF adapter
                pairwise_task_id = f"{doc_id_1}.vs.{doc_id_2}.pairwise.{trial}.{self.config.model}.{uuid4().hex[:6]}"
                gen_config = GenerationConfig(
                    provider=provider,
                    model=base_model,
                    extra={
                        "max_completion_tokens": self.config.max_tokens,
                        "temperature": self.config.temperature,
                        "thinking_budget_tokens": self.config.thinking_budget_tokens,
                        "json_output": True,  # Eval responses are JSON, skip 3KB minimum check
                        "timeout": self.config.timeout_seconds,
                        "task_id": pairwise_task_id,
                        "run_id": self.run_id,  # For log correlation
                        "phase": "fpf.evaluate",  # For evaluation log categorization
                        "document_id": doc_id_1,  # For per-document log context (use doc_a as anchor)
                        "key_mode": self.config.key_mode,
                    },
                )

                # INSTRUMENTATION: Log before FPF dispatch
                logger.info(
                    "[EVAL-DISPATCH] pairwise_eval task_id=%s provider=%s model=%s max_completion_tokens=%s "
                    "temperature=%s thinking_budget_tokens=%s timeout=%ss hard_timeout=%ss key_mode=%s json_output=%s retries=%s",
                    pairwise_task_id,
                    provider,
                    base_model,
                    self.config.max_tokens,
                    self.config.temperature,
                    self.config.thinking_budget_tokens,
                    self.config.timeout_seconds,
                    self.config.timeout_seconds + 30,
                    self.config.key_mode,
                    True,
                    self.config.retries,
                )

                # Call FPF for pairwise evaluation with hard timeout
                # Apply provider-level rate limiting before making API call
                # NOTE: FPF has its own retry logic for API errors (429, 500s) - don't retry those here
                try:
                    async with RateLimitedRequest(provider):
                        result = await asyncio.wait_for(
                            self.fpf.generate(
                                query=prompt,
                                config=gen_config,
                                user_uuid=self.user_uuid,
                            ),
                            timeout=float(self.config.timeout_seconds + 30),
                        )
                except asyncio.TimeoutError:
                    logger.error(f"[EVAL-DISPATCH] HARD TIMEOUT: FPF pairwise_eval call for {pairwise_task_id} exceeded {self.config.timeout_seconds + 30}s")
                    # Timeout is fatal - FPF already timed out internally, don't retry
                    raise RuntimeError(f"Pairwise eval call timed out after {self.config.timeout_seconds + 30}s for {pairwise_task_id}")
                except RuntimeError as fpf_err:
                    # RuntimeError from FPF means API failure after FPF's own retries - don't retry again
                    logger.error(f"[EVAL-DISPATCH] FPF API error (not retriable): {fpf_err}")
                    if self.stats:
                        self.stats.record_failure(str(fpf_err))
                    raise

                logger.info(f"[EVAL-DISPATCH] FPF pairwise_eval completed for {pairwise_task_id}")

                raw_response = result.content

                # Parse JSON response - THESE errors ARE retriable (malformed LLM output)
                try:
                    data = _parse_json_response(raw_response)

                    # Extract winner
                    winner_letter = data.get("winner", "").upper()
                    if winner_letter not in ("A", "B"):
                        raise ValueError(f"Invalid winner: {winner_letter}")

                    # Map A/B back to actual doc IDs
                    winner_doc_id = doc_id_1 if winner_letter == "A" else doc_id_2
                    reason = data.get("reason", "")
                except (ValueError, KeyError, TypeError, json.JSONDecodeError) as parse_err:
                    # Parse/validation errors - these ARE retriable at eval level
                    last_error = parse_err
                    if attempt < self.config.retries:
                        if self.stats:
                            self.stats.record_retry(attempt + 1, f"Parse error: {parse_err}")
                        logger.warning(f"Pairwise eval attempt {attempt + 1} parse error: {parse_err}")
                        continue  # Retry with a fresh FPF call
                    else:
                        if self.stats:
                            self.stats.record_failure(f"Parse error: {parse_err}")
                        raise RuntimeError(f"Pairwise evaluation failed after {self.config.retries + 1} attempts: {parse_err}")

                # Track success
                if self.stats:
                    self.stats.record_success()

                completed_at = datetime.utcnow()
                return PairwiseResult(
                    doc_id_1=doc_id_1,
                    doc_id_2=doc_id_2,
                    winner_doc_id=winner_doc_id,
                    model=self.config.model,
                    trial=trial,
                    reason=reason,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_seconds=(completed_at - started_at).total_seconds(),
                    raw_response=raw_response,
                    tokens_in=result.input_tokens,
                    tokens_out=result.output_tokens,
                    tokens_thinking=_extract_thinking_tokens(getattr(result, "metadata", None)),
                )

            except RuntimeError:
                # Already handled above - propagate up
                raise
            except Exception as e:
                # Unexpected errors - log and propagate
                logger.error(f"Unexpected error in pairwise_eval: {e}")
                if self.stats:
                    self.stats.record_failure(str(e))
                raise

        raise RuntimeError(
            f"Pairwise evaluation failed after {self.config.retries + 1} attempts: {last_error}"
        )
