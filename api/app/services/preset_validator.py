"""
Preset Validation Service.

Validates preset configuration completeness and correctness before execution.
All required fields must be set - NO FALLBACKS.
"""
import logging
from typing import List, Optional
from app.infra.db.models.preset import Preset
from app.services.config_builder import (
    derive_canonical_preset_config_state,
    extract_combine_models,
    extract_judge_models,
    normalize_combine_config,
    normalize_aiq_config,
    normalize_config_overrides,
    normalize_eval_config,
)

logger = logging.getLogger(__name__)


class PresetValidationError(ValueError):
    """Raised when preset validation fails."""

    def __init__(self, errors: List[str]):
        self.errors = errors
        message = "Preset validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        super().__init__(message)


class PresetValidator:
    """Validates preset configuration completeness and correctness."""

    def validate_preset(self, preset: Preset) -> List[str]:
        """
        Validate preset and return list of errors.
        Empty list = valid preset.

        Args:
            preset: Preset model to validate

        Returns:
            List of error messages (empty if valid)
        """
        errors = []
        overrides = normalize_config_overrides(getattr(preset, "config_overrides", None) or {})
        canonical_state = derive_canonical_preset_config_state(config_overrides=overrides)
        aiq_config = normalize_aiq_config(overrides.get("aiq"))
        eval_config = normalize_eval_config(overrides.get("eval"))
        combine_config = normalize_combine_config(overrides.get("combine"))
        general_config = canonical_state["general_config"]
        concurrency_config = canonical_state["concurrency_config"]

        generators = canonical_state["generators"]
        models = canonical_state["models"]
        evaluation_enabled = canonical_state["evaluation_enabled"]
        pairwise_enabled = canonical_state["pairwise_enabled"]
        combine_enabled = bool(extract_combine_models(combine_config))

        # =====================================================================
        # Required Core Fields
        # =====================================================================

        if not preset.name or not preset.name.strip():
            errors.append("name is required and cannot be empty")

        # =====================================================================
        # Timing Configuration (timeouts are optional/unbounded)
        # =====================================================================

        # =====================================================================
        # Required Concurrency Configuration
        # =====================================================================

        generation_concurrency = concurrency_config.get("generation_concurrency")
        if generation_concurrency is None:
            errors.append("generation_concurrency is required")
        elif not (1 <= generation_concurrency <= 50):
            errors.append(f"generation_concurrency must be 1-50, got {generation_concurrency}")

        eval_concurrency = concurrency_config.get("eval_concurrency")
        if eval_concurrency is None:
            errors.append("eval_concurrency is required")
        elif not (1 <= eval_concurrency <= 50):
            errors.append(f"eval_concurrency must be 1-50, got {eval_concurrency}")

        # =====================================================================
        # Required Iteration Configuration
        # =====================================================================

        # Note: iterations field exists but may need to be validated separately
        # since it was moved to the new config section

        eval_iterations = eval_config.get("iterations")
        if eval_iterations is None:
            errors.append("eval_iterations is required")
        elif not (1 <= eval_iterations <= 10):
            errors.append(f"eval_iterations must be 1-10, got {eval_iterations}")

        pairwise_top_n = eval_config.get("pairwise_top_n")
        if pairwise_top_n is not None and pairwise_top_n < 2:
            errors.append(f"pairwise_top_n must be >= 2 or None, got {pairwise_top_n}")

        # =====================================================================
        # Optional Fields Validation
        # =====================================================================

        post_combine_top_n = general_config.get("post_combine_top_n")
        if post_combine_top_n is not None and post_combine_top_n < 2:
            errors.append(f"post_combine_top_n must be >= 2 or None, got {post_combine_top_n}")

        # =====================================================================
        # Conditional Requirements Based on Enabled Features
        # =====================================================================

        # Check if generators include FPF
        if 'fpf' in generators or 'FPF' in generators:
            if not preset.generation_instructions_id:
                errors.append(
                    "generation_instructions_id required when FPF generator enabled. "
                    "Select instructions from Content Library."
                )

        if "ma" in generators or "MA" in generators:
            errors.append("MA generator is not supported yet. Remove MA model selections.")
        if "aiq" in generators or "AIQ" in generators:
            if not preset.generation_instructions_id:
                errors.append(
                    "generation_instructions_id required when AI-Q generator enabled. "
                    "Select instructions from Content Library."
                )
            if not list(aiq_config.get("selected_models") or []):
                errors.append("aiq_config.selected_models is required when AI-Q is enabled")
            if aiq_config.get("agent_type") != "deep_researcher":
                errors.append("AI-Q v1 only supports agent_type='deep_researcher'")
            if not aiq_config.get("web_only", True):
                errors.append("AI-Q v1 only supports web_only=true")
            data_sources = list(aiq_config.get("data_sources") or [])
            if set(data_sources) != {"web"}:
                errors.append("AI-Q v1 only supports data_sources=['web']")
            if not aiq_config.get("profile"):
                errors.append("aiq_config.profile is required when AI-Q is enabled")

        # Check evaluation settings
        if evaluation_enabled:
            if not preset.eval_criteria_id:
                errors.append(
                    "eval_criteria_id required when evaluation enabled. "
                    "Select criteria from Content Library."
                )
            if not preset.single_eval_instructions_id:
                errors.append(
                    "single_eval_instructions_id required when evaluation enabled. "
                    "Select instructions from Content Library."
                )
            if not extract_judge_models(eval_config.get("judge_models"), eval_config.get("eval_model")):
                errors.append("eval_judge_models required when evaluation enabled")

        # Check pairwise settings
        if pairwise_enabled:
            if not preset.pairwise_eval_instructions_id:
                errors.append(
                    "pairwise_eval_instructions_id required when pairwise enabled. "
                    "Select instructions from Content Library."
                )
            if not preset.eval_criteria_id:
                errors.append(
                    "eval_criteria_id required when pairwise enabled. "
                    "Select criteria from Content Library."
                )

        # Check combine settings
        if combine_enabled:
            if not preset.combine_instructions_id:
                errors.append(
                    "combine_instructions_id required when combine models are selected. "
                    "Select instructions from Content Library."
                )
            if not combine_config.get("strategy"):
                errors.append("combine strategy required when combine models are selected")

        # =====================================================================
        # Input Validation
        # =====================================================================

        if not preset.documents:
            errors.append(
                "At least one input document required. "
                "Add documents to the preset."
            )

        if not models:
            errors.append("At least one model required. Add models to the preset.")

        if not generators:
            errors.append("At least one generator required. Select FPF, GPTR, DR, or AI-Q.")

        return errors

    def validate_or_raise(self, preset: Preset) -> None:
        """
        Validate preset and raise PresetValidationError if invalid.

        Args:
            preset: Preset model to validate

        Raises:
            PresetValidationError: If validation fails
        """
        errors = self.validate_preset(preset)
        if errors:
            logger.warning("[CONFIG] Preset %r validation failed: %s", getattr(preset, 'name', '?'), "; ".join(errors))
            raise PresetValidationError(errors)

    def validate_for_run_execution(self, preset: Preset) -> None:
        """
        More strict validation specifically for run execution.
        Ensures all fields needed to create a RunConfig are present.

        Args:
            preset: Preset model to validate

        Raises:
            PresetValidationError: If validation fails
        """
        # Run all basic validations first
        errors = self.validate_preset(preset)

        # Additional checks specific to run execution
        # (Add more as needed based on RunConfig requirements)

        if errors:
            raise PresetValidationError(errors)
