"""
GitHubConnection SQLAlchemy model.

Stores repository connection metadata for reading input files and writing
output files. Secrets are not stored here; GitHub uses root .env GITHUB_TOKEN.
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base


def generate_id() -> str:
    return str(uuid.uuid4())


class GitHubConnection(Base):
    """
    Stores GitHub repository connection information.

    Used for:
    - Reading input documents from a GitHub repository
    - Writing generated outputs back to GitHub
    - Importing content (instructions, criteria) from GitHub
    """

    __tablename__ = "github_connections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    user_uuid: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(default=None, onupdate=datetime.utcnow)

    # Connection info
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    repo: Mapped[str] = mapped_column(String(255), nullable=False)  # "owner/repo"
    branch: Mapped[str] = mapped_column(String(100), default="main")

    # Token storage is disabled in self-hosted mode. This records the env var used.
    token_ref: Mapped[str] = mapped_column(Text, nullable=False, default="env:GITHUB_TOKEN")

    # Connection status
    last_tested_at: Mapped[Optional[datetime]] = mapped_column(default=None)
    is_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<GitHubConnection(id={self.id}, repo={self.repo}, branch={self.branch})>"
