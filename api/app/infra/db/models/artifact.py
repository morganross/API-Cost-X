"""
Artifact SQLAlchemy model.

An Artifact is an output produced by a Task - typically a generated report or evaluation.
"""
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.db.base import Base


def generate_id() -> str:
    return str(uuid.uuid4())


class ArtifactType(str, Enum):
    """Types of artifacts that can be generated."""
    REPORT = "report"           # Full GPTR report
    EVALUATION = "evaluation"   # Evaluation results
    SUMMARY = "summary"         # Summary text
    RAW_RESPONSE = "raw"        # Raw LLM response
    LOG = "log"                 # Execution log


class Artifact(Base):
    """
    An output artifact from a generation Task.

    A Task may produce multiple Artifacts (report + evaluation + logs, etc.)
    """

    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Parent task
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)

    # Artifact info
    artifact_type: Mapped[str] = mapped_column(String(50), default=ArtifactType.REPORT.value)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Content - either stored directly or via file path
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

    # Metadata
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # SHA256
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    mime_type: Mapped[str] = mapped_column(String(100), default="text/markdown")

    # For evaluation artifacts: store structured scores
    scores: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Example: {"accuracy": 0.85, "completeness": 0.90, "relevance": 0.88}

    # Average score for quick filtering/sorting
    avg_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Relationships
    task: Mapped["Task"] = relationship("Task", back_populates="artifacts")

    def __repr__(self) -> str:
        return f"<Artifact(id={self.id}, type={self.artifact_type}, name={self.name})>"
