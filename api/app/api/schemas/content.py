"""
Pydantic schemas for Content API.
"""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ContentType(str, Enum):
    """Types of content that can be stored."""
    GENERATION_INSTRUCTIONS = "generation_instructions"
    INPUT_DOCUMENT = "input_document"
    SINGLE_EVAL_INSTRUCTIONS = "single_eval_instructions"
    PAIRWISE_EVAL_INSTRUCTIONS = "pairwise_eval_instructions"
    EVAL_CRITERIA = "eval_criteria"
    COMBINE_INSTRUCTIONS = "combine_instructions"
    TEMPLATE_FRAGMENT = "template_fragment"

    # Output phase - winning documents from completed runs
    OUTPUT_DOCUMENT = "output_document"

    # Virtual, read-only content generated from per-run sidecar logs
    LOGS = "logs"


# ============================================================================
# Request Schemas
# ============================================================================

class ContentCreate(BaseModel):
    """Request to create new content."""
    name: str = Field(..., min_length=1, max_length=255, description="Content name")
    content_type: ContentType = Field(..., description="Type of content")
    body: str = Field(..., min_length=1, description="The actual content text")
    variables: dict[str, Optional[str]] = Field(
        default_factory=dict,
        description="Variable mappings: {VAR_NAME: content_id or null for runtime}"
    )
    description: Optional[str] = Field(None, max_length=2000)
    tags: list[str] = Field(default_factory=list)


class ContentUpdate(BaseModel):
    """Request to update content."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    body: Optional[str] = Field(None, min_length=1)
    variables: Optional[dict[str, Optional[str]]] = None
    description: Optional[str] = Field(None, max_length=2000)
    tags: Optional[list[str]] = None


class ContentResolveRequest(BaseModel):
    """Request to resolve/preview content with variables."""
    runtime_variables: dict[str, str] = Field(
        default_factory=dict,
        description="Runtime variables to substitute: {INPUT: 'actual content'}"
    )


# ============================================================================
# Response Schemas
# ============================================================================

class ContentSummary(BaseModel):
    """Summary of content (for list views)."""
    id: str
    name: str
    content_type: ContentType
    description: Optional[str] = None
    tags: list[str] = []
    body_preview: str = Field(default="", description="First 200 chars of body")
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ContentDetail(BaseModel):
    """Full content details."""
    id: str
    name: str
    content_type: ContentType
    body: str
    variables: dict[str, Optional[str]] = {}
    description: Optional[str] = None
    tags: list[str] = []
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ContentList(BaseModel):
    """Paginated list of contents."""
    items: list[ContentSummary]
    total: int
    page: int
    page_size: int
    pages: int


class ContentResolved(BaseModel):
    """Content with all variables resolved."""
    id: str
    name: str
    content_type: ContentType
    resolved_body: str
    unresolved_variables: list[str] = Field(
        default_factory=list,
        description="Variables that could not be resolved"
    )


class ContentTypeCounts(BaseModel):
    """Count of contents by type."""
    generation_instructions: int = 0
    input_document: int = 0
    single_eval_instructions: int = 0
    pairwise_eval_instructions: int = 0
    eval_criteria: int = 0
    combine_instructions: int = 0
    template_fragment: int = 0
    logs: int = 0
    total: int = 0
