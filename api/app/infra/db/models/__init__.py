"""
SQLAlchemy models for APICostX database.

Exports all models for easy importing.
"""
from app.infra.db.base import Base

# Import all models so they're registered with Base
from app.infra.db.models.preset import Preset
from app.infra.db.models.run import Run, RunStatus
from app.infra.db.models.document import Document
from app.infra.db.models.task import Task, TaskStatus
from app.infra.db.models.artifact import Artifact, ArtifactType
from app.infra.db.models.content import Content, ContentType
from app.infra.db.models.github_connection import GitHubConnection
from app.infra.db.models.user_meta import UserMeta
from app.infra.db.models.user_settings import UserSettings

__all__ = [
    "Base",
    "Preset",
    "Run",
    "RunStatus",
    "Document",
    "Task",
    "TaskStatus",
    "Artifact",
    "ArtifactType",
    "Content",
    "ContentType",
    "GitHubConnection",
    "UserMeta",
    "UserSettings",
]
