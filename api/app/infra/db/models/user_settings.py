"""
User settings model.

Stores arbitrary local settings as JSON in the user's SQLite DB.
"""
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base


class UserSettings(Base):
    """Per-user settings stored as JSON."""

    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_uuid: Mapped[str] = mapped_column(String(36), nullable=False, index=True, unique=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
