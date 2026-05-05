"""
Document SQLAlchemy model.

A Document is a source file to be processed by generators.
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base


def generate_id() -> str:
    return str(uuid.uuid4())


class Document(Base):
    """
    A source document for evaluation.

    Documents are the input files that get processed by FPF/GPTR.
    """

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    user_uuid: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)  # Multi-tenancy: owner user UUID
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(default=None, onupdate=datetime.utcnow)

    # Basic info
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)  # Relative or absolute path

    # Content (optional - can store content directly or just reference file)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # SHA256

    # Metadata
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    file_type: Mapped[str] = mapped_column(String(50), default="markdown")

    # Categorization
    tags: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Comma-separated

    def __repr__(self) -> str:
        return f"<Document(id={self.id}, name={self.name})>"
