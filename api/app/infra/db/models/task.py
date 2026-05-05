"""
Task SQLAlchemy model.

A Task represents a single generation job within a Run.
Each Run may have multiple Tasks (one per document × model combination).
"""
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.db.base import Base


def generate_id() -> str:
    return str(uuid.uuid4())


class TaskStatus(str, Enum):
    """Task execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"  # Intentionally excluded from resume


class Task(Base):
    """
    A single generation task within a Run.

    For example, if a Run has 2 documents × 3 models × 2 iterations,
    that creates 12 Tasks.
    """

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Parent run
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)

    # Task specification
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)  # Reference to Document
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)  # e.g., "gpt-4o-mini"
    iteration: Mapped[int] = mapped_column(Integer, default=1)

    # Status
    status: Mapped[str] = mapped_column(String(20), default=TaskStatus.PENDING.value)
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100

    # Error info
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timing
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Token counters
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)

    # Phase and generator tracking (added for run resume feature)
    phase: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # 'generation', 'single_eval', 'pairwise', 'combine'
    generator: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # 'fpf', 'gptr', 'dr', 'aiq' (null for eval)
    output_ref: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # Relative path to output file on disk
    judge_model: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)  # For eval tasks
    retry_count: Mapped[int] = mapped_column(Integer, default=0)  # How many times this task has been retried

    # Relationships
    run: Mapped["Run"] = relationship("Run", back_populates="tasks")
    artifacts: Mapped[list["Artifact"]] = relationship("Artifact", back_populates="task", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Task(id={self.id}, model={self.model_name}, status={self.status})>"
