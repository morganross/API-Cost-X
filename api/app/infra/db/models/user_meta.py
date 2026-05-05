"""
User metadata model.

Stores local seed status, version, and profile info inside the user's SQLite DB.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base


class UserMeta(Base):
    """Per-user metadata for readiness gating and seeding."""

    __tablename__ = "user_meta"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    # UUID is the ONLY user identifier — API service does not store email
    uuid: Mapped[str] = mapped_column(String(36), nullable=False, index=True, unique=True)

    # Seed status
    seed_status: Mapped[str] = mapped_column(String(32), nullable=False)
    seed_version: Mapped[str] = mapped_column(String(64), nullable=False)

    seeded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
