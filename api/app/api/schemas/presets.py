"""
API Schemas for Presets.

Presets are saved configurations for runs that can be reused.
"""
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .runs import (
    # Complete config types for preset persistence
    FpfConfigComplete, GptrConfigComplete, DrConfigComplete, MaConfigComplete, AiqConfigComplete,
    EvalConfigComplete, PairwiseConfigComplete, CombineConfigComplete,
    GeneralConfigComplete, ConcurrencyConfigComplete,
)


class OutputDestination(str, Enum):
    """Where winning documents are written."""
    NONE = "none"           # Don't save outputs
    LIBRARY = "library"     # Save to Content Library as OUTPUT_DOCUMENT (default)
    GITHUB = "github"       # Also push to GitHub repository


# ============================================================================
# Request Models
# ============================================================================

def _normalize_deprecated_key_mode(data: Any) -> Any:
    """Accept stale preset clients that still send top-level key_mode."""
    if not isinstance(data, dict) or "key_mode" not in data:
        return data

    normalized = dict(data)
    key_mode = normalized.pop("key_mode", None)
    general_config = normalized.get("general_config")

    if key_mode in {"byok", "system"} and isinstance(general_config, dict):
        normalized["general_config"] = {
            **general_config,
            "use_byok_first": key_mode == "byok",
        }

    return normalized


class PresetCreate(BaseModel):
    """Request to create a new preset."""
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def normalize_deprecated_key_mode(cls, data: Any) -> Any:
        return _normalize_deprecated_key_mode(data)

    name: str = Field(..., min_length=1, max_length=200, description="Preset name")
    description: Optional[str] = Field(None, max_length=2000)

    # Default documents to include
    documents: list[str] = Field(
        default_factory=list,
        description="Default document IDs for this preset"
    )

    # Content Library instruction IDs
    single_eval_instructions_id: Optional[str] = Field(None, description="Content ID for single eval instructions")
    pairwise_eval_instructions_id: Optional[str] = Field(None, description="Content ID for pairwise eval instructions")
    eval_criteria_id: Optional[str] = Field(None, description="Content ID for evaluation criteria")
    combine_instructions_id: Optional[str] = Field(None, description="Content ID for combine instructions")
    generation_instructions_id: Optional[str] = Field(None, description="Content ID for FPF generation instructions")

    # Complete configuration objects (NEW - for full preset persistence)
    general_config: Optional[GeneralConfigComplete] = None
    fpf_config: Optional[FpfConfigComplete] = None
    gptr_config: Optional[GptrConfigComplete] = None
    dr_config: Optional[DrConfigComplete] = None
    ma_config: Optional[MaConfigComplete] = None
    aiq_config: Optional[AiqConfigComplete] = None
    eval_config: Optional[EvalConfigComplete] = None
    pairwise_config: Optional[PairwiseConfigComplete] = None
    combine_config: Optional[CombineConfigComplete] = None
    concurrency_config: Optional[ConcurrencyConfigComplete] = None

    # GitHub input source configuration
    input_source_type: Optional[str] = Field(None, description="Input source: 'database' or 'github'")
    github_connection_id: Optional[str] = Field(None, description="GitHub connection ID for input")
    github_input_paths: Optional[list[str]] = Field(default=None, description="Paths in GitHub repo to use as input")
    github_output_path: Optional[str] = Field(None, description="Path in GitHub repo for output")

    # Output configuration
    output_destination: Optional[OutputDestination] = Field(
        default=OutputDestination.LIBRARY,
        description="Where to save winning documents: 'none', 'library', or 'github'"
    )
    output_filename_template: Optional[str] = Field(
        default="{source_doc_name}_{winner_model}_{timestamp}",
        description="Template for output filenames"
    )
    github_commit_message: Optional[str] = Field(
        default="APICostX: Add winning document",
        description="Commit message when pushing to GitHub"
    )
    prepend_source_first_line_frontmatter: bool = Field(
        default=False,
        description="Prepend source doc first line as YAML frontmatter to generated outputs"
    )
class PresetUpdate(BaseModel):
    """Request to update a preset."""
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def normalize_deprecated_key_mode(cls, data: Any) -> Any:
        return _normalize_deprecated_key_mode(data)

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    documents: Optional[list[str]] = None

    # Content Library instruction IDs
    single_eval_instructions_id: Optional[str] = None
    pairwise_eval_instructions_id: Optional[str] = None
    eval_criteria_id: Optional[str] = None
    combine_instructions_id: Optional[str] = None
    generation_instructions_id: Optional[str] = None

    # Complete configuration objects (NEW)
    general_config: Optional[GeneralConfigComplete] = None
    fpf_config: Optional[FpfConfigComplete] = None
    gptr_config: Optional[GptrConfigComplete] = None
    dr_config: Optional[DrConfigComplete] = None
    ma_config: Optional[MaConfigComplete] = None
    aiq_config: Optional[AiqConfigComplete] = None
    eval_config: Optional[EvalConfigComplete] = None
    pairwise_config: Optional[PairwiseConfigComplete] = None
    combine_config: Optional[CombineConfigComplete] = None
    concurrency_config: Optional[ConcurrencyConfigComplete] = None

    # GitHub input source configuration
    input_source_type: Optional[str] = Field(None, description="Input source: 'database' or 'github'")
    github_connection_id: Optional[str] = Field(None, description="GitHub connection ID for input")
    github_input_paths: Optional[list[str]] = Field(default=None, description="Paths in GitHub repo to use as input")
    github_output_path: Optional[str] = Field(None, description="Path in GitHub repo for output")

    # Output configuration
    output_destination: Optional[OutputDestination] = Field(
        None,
        description="Where to save winning documents: 'none', 'library', or 'github'"
    )
    output_filename_template: Optional[str] = Field(
        None,
        description="Template for output filenames"
    )
    github_commit_message: Optional[str] = Field(
        None,
        description="Commit message when pushing to GitHub"
    )
    prepend_source_first_line_frontmatter: Optional[bool] = Field(
        None,
        description="Prepend source doc first line as YAML frontmatter to generated outputs"
    )
# ============================================================================
# Response Models
# ============================================================================

class PresetResponse(BaseModel):
    """Full preset response."""
    id: str
    name: str
    description: Optional[str] = None

    documents: list[str]

    # Content Library instruction IDs
    single_eval_instructions_id: Optional[str] = None
    pairwise_eval_instructions_id: Optional[str] = None
    eval_criteria_id: Optional[str] = None
    combine_instructions_id: Optional[str] = None
    generation_instructions_id: Optional[str] = None

    # Complete configuration objects (NEW)
    general_config: Optional[GeneralConfigComplete] = None
    fpf_config: Optional[FpfConfigComplete] = None
    gptr_config: Optional[GptrConfigComplete] = None
    dr_config: Optional[DrConfigComplete] = None
    ma_config: Optional[MaConfigComplete] = None
    aiq_config: Optional[AiqConfigComplete] = None
    eval_config: Optional[EvalConfigComplete] = None
    pairwise_config: Optional[PairwiseConfigComplete] = None
    combine_config: Optional[CombineConfigComplete] = None
    concurrency_config: Optional[ConcurrencyConfigComplete] = None

    # GitHub input source configuration
    input_source_type: Optional[str] = None
    github_connection_id: Optional[str] = None
    github_input_paths: Optional[list[str]] = None
    github_output_path: Optional[str] = None

    # Output configuration
    output_destination: OutputDestination
    output_filename_template: Optional[str] = None
    github_commit_message: Optional[str] = None
    prepend_source_first_line_frontmatter: bool

    created_at: datetime
    updated_at: Optional[datetime] = None

    # Statistics
    run_count: int = 0
    last_run_at: Optional[datetime] = None
    runnable: bool = False
    validation_errors: list[str] = Field(default_factory=list)

    class Config:
        from_attributes = True  # Enable ORM mode


class PresetSummary(BaseModel):
    """Summary preset for list views."""
    id: str
    name: str
    description: Optional[str] = None
    document_count: int = 0
    model_count: int = 0
    created_at: datetime
    updated_at: Optional[datetime] = None
    run_count: int = 0
    runnable: bool = False

    class Config:
        from_attributes = True


class PresetList(BaseModel):
    """Paginated list of presets."""
    items: list[PresetSummary]
    total: int
    page: int
    page_size: int
    pages: int


class PresetRunnableResponse(BaseModel):
    """Runtime launch readiness for a preset."""
    preset_id: str
    runnable: bool
    validation_errors: list[str] = Field(default_factory=list)
