"""
FPF-specific configuration options.
"""
from typing import Optional

from pydantic import BaseModel, Field


class FpfConfig(BaseModel):
    """Configuration for FilePromptForge adapter."""

    # FPF-specific options
    reasoning_effort: Optional[str] = Field(
        "medium",
        description="Reasoning effort level (low, medium, high)"
    )

    max_completion_tokens: Optional[int] = Field(
        50000,
        description="Maximum completion tokens for FPF"
    )

    # Template options
    prompt_template: Optional[str] = Field(
        None,
        description="Custom prompt template path"
    )

    # Output options
    json_output: bool = Field(
        False,
        description="Request JSON output from FPF"
    )

    # Web search options
    search_context_size: Optional[str] = Field(
        "medium",
        description="Web search context size (small, medium, large)"
    )
