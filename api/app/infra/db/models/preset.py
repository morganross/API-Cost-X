"""
Preset SQLAlchemy model.

A Preset is a saved configuration for running evaluations.
"""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.db.base import Base

if TYPE_CHECKING:
    from app.infra.db.models.run import Run
    from app.infra.db.models.content import Content


def generate_id() -> str:
    return str(uuid.uuid4())


class Preset(Base):
    """
    A saved configuration/preset for running evaluations.

    Presets define:
    - Which documents to process
    - Which instruction content to use
    - The canonical runtime configuration in config_overrides
    """

    __tablename__ = "presets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    user_uuid: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)  # Multi-tenancy: owner user UUID
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(default=None, onupdate=datetime.utcnow)

    # Basic info
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Default input document IDs for this preset
    documents: Mapped[list] = mapped_column(JSON, default=list)

    # Canonical runtime truth
    config_overrides: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Generation phase
    generation_instructions_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("contents.id"), nullable=True
    )

    # Evaluation phase
    single_eval_instructions_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("contents.id"), nullable=True
    )
    pairwise_eval_instructions_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("contents.id"), nullable=True
    )
    eval_criteria_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("contents.id"), nullable=True
    )

    # Combine phase
    combine_instructions_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("contents.id"), nullable=True
    )

    # Relationships
    runs: Mapped[list["Run"]] = relationship("Run", back_populates="preset")

    # Content relationships
    generation_instructions: Mapped[Optional["Content"]] = relationship(
        "Content", foreign_keys=[generation_instructions_id]
    )
    single_eval_instructions: Mapped[Optional["Content"]] = relationship(
        "Content", foreign_keys=[single_eval_instructions_id]
    )
    pairwise_eval_instructions: Mapped[Optional["Content"]] = relationship(
        "Content", foreign_keys=[pairwise_eval_instructions_id]
    )
    eval_criteria: Mapped[Optional["Content"]] = relationship(
        "Content", foreign_keys=[eval_criteria_id]
    )
    combine_instructions: Mapped[Optional["Content"]] = relationship(
        "Content", foreign_keys=[combine_instructions_id]
    )

    def __repr__(self) -> str:
        return f"<Preset(id={self.id}, name={self.name})>"
