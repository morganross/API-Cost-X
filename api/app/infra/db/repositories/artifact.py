"""
Artifact repository for CRUD operations on artifacts.
"""
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models.artifact import Artifact, ArtifactType
from app.infra.db.repositories.base import BaseRepository


class ArtifactRepository(BaseRepository[Artifact]):
    """Repository for Artifact CRUD operations."""

    def __init__(self, session: AsyncSession):
        super().__init__(Artifact, session)

    async def get_by_task(self, task_id: str) -> Sequence[Artifact]:
        """Get all artifacts for a specific task."""
        stmt = (
            select(Artifact)
            .where(Artifact.task_id == task_id)
            .order_by(Artifact.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_by_type(
        self,
        task_id: str,
        artifact_type: ArtifactType
    ) -> Sequence[Artifact]:
        """Get artifacts of a specific type for a task."""
        stmt = (
            select(Artifact)
            .where(Artifact.task_id == task_id)
            .where(Artifact.artifact_type == artifact_type.value)
            .order_by(Artifact.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_reports_for_task(self, task_id: str) -> Sequence[Artifact]:
        """Get all report artifacts for a task."""
        return await self.get_by_type(task_id, ArtifactType.REPORT)

    async def get_evaluations_for_task(self, task_id: str) -> Sequence[Artifact]:
        """Get all evaluation artifacts for a task."""
        return await self.get_by_type(task_id, ArtifactType.EVALUATION)

    async def create_report(
        self,
        task_id: str,
        name: str,
        content: str,
        content_hash: Optional[str] = None,
        file_path: Optional[str] = None
    ) -> Artifact:
        """Create a report artifact."""
        return await self.create(
            task_id=task_id,
            artifact_type=ArtifactType.REPORT.value,
            name=name,
            content=content,
            content_hash=content_hash,
            file_path=file_path,
            size_bytes=len(content.encode('utf-8')),
            mime_type="text/markdown"
        )

    async def create_evaluation(
        self,
        task_id: str,
        name: str,
        scores: dict,
        content: Optional[str] = None
    ) -> Artifact:
        """Create an evaluation artifact with scores."""
        # Calculate average score
        score_values = [v for v in scores.values() if isinstance(v, (int, float))]
        avg_score = sum(score_values) / len(score_values) if score_values else None

        return await self.create(
            task_id=task_id,
            artifact_type=ArtifactType.EVALUATION.value,
            name=name,
            content=content or "",
            scores=scores,
            avg_score=avg_score,
            size_bytes=len((content or "").encode('utf-8')),
            mime_type="application/json"
        )

    async def get_top_scoring(
        self,
        limit: int = 10,
        artifact_type: ArtifactType = ArtifactType.EVALUATION
    ) -> Sequence[Artifact]:
        """Get top scoring artifacts (evaluations by default)."""
        stmt = (
            select(Artifact)
            .where(Artifact.artifact_type == artifact_type.value)
            .where(Artifact.avg_score.isnot(None))
            .order_by(Artifact.avg_score.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()
