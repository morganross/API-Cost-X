"""
Content SQLAlchemy model.

Content stores all text-based content used in the APICostX pipeline:
- Generation instructions (prompts for generating outputs)
- Single evaluation instructions (how to rate individual outputs)
- Pairwise evaluation instructions (how to compare two outputs)
- Evaluation criteria (rubrics and scoring guides)
- Combine instructions (how to merge outputs)
- Template fragments (reusable text snippets with variables)
- Input documents (source documents stored in DB)
"""
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import JSON, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base


def generate_id() -> str:
    return str(uuid.uuid4())


class ContentType(str, Enum):
    """Types of content that can be stored."""

    # Generation phase
    GENERATION_INSTRUCTIONS = "generation_instructions"
    INPUT_DOCUMENT = "input_document"

    # Evaluation phase
    SINGLE_EVAL_INSTRUCTIONS = "single_eval_instructions"
    PAIRWISE_EVAL_INSTRUCTIONS = "pairwise_eval_instructions"
    EVAL_CRITERIA = "eval_criteria"

    # Combine phase
    COMBINE_INSTRUCTIONS = "combine_instructions"

    # Reusable fragments for variable interpolation
    TEMPLATE_FRAGMENT = "template_fragment"

    # Output phase - winning documents from completed runs
    OUTPUT_DOCUMENT = "output_document"


class Content(Base):
    """
    Stores text content used throughout the APICostX pipeline.

    Content pieces can reference other content through variables.
    Variables use Mustache-style syntax: {{VARIABLE_NAME}}

    Static variables are resolved from other Content pieces.
    Runtime variables (INPUT, OUTPUT_A, OUTPUT_B, etc.) are resolved at execution.
    """

    __tablename__ = "contents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    user_uuid: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)  # Multi-tenancy: owner user UUID
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(default=None, onupdate=datetime.utcnow)

    # Basic info
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    content_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # The actual content body
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # Variable mappings: {"VAR_NAME": "content_id"} for static variables
    # Runtime variables (INPUT, OUTPUT_A, etc.) are not listed here
    variables: Mapped[dict] = mapped_column(JSON, default=dict)

    # Metadata
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)

    def __repr__(self) -> str:
        return f"<Content(id={self.id}, name={self.name}, type={self.content_type})>"

    def get_content_type_enum(self) -> ContentType:
        """Get the content_type as an enum value."""
        return ContentType(self.content_type)
