"""
SQLAlchemy base class and common utilities.
"""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


def _generate_uuid() -> str:
    """Generate a new UUID4 string for primary keys."""
    return str(uuid.uuid4())


# Naming convention for constraints (makes migrations cleaner)
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=convention)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    metadata = metadata

    # Common columns for all tables
    id: Mapped[str] = mapped_column(primary_key=True, default=_generate_uuid)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(default=None, onupdate=func.now())

    def to_dict(self) -> dict[str, Any]:
        """Convert model to dictionary."""
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class NormalizedBase(DeclarativeBase):
    """
    Base for normalized run-result tables.

    Does NOT auto-add id, created_at, updated_at columns — each model
    defines exactly the columns that match the DDL in schema_bootstrap.py.
    Shares the same MetaData registry as Base so all tables coexist on one engine.
    """

    metadata = metadata

    def to_dict(self) -> dict[str, Any]:
        """Convert model to dictionary."""
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}
