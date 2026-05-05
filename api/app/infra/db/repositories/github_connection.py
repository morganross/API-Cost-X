"""
GitHubConnection repository for CRUD operations on GitHub connections.
"""
from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models.github_connection import GitHubConnection
from app.infra.db.repositories.base import BaseRepository


class GitHubConnectionRepository(BaseRepository[GitHubConnection]):
    """Repository for GitHubConnection CRUD operations."""

    def __init__(self, session: AsyncSession, user_uuid: Optional[str] = None):
        super().__init__(GitHubConnection, session, user_uuid)

    async def get_active(
        self,
        limit: int = 100,
        offset: int = 0
    ) -> Sequence[GitHubConnection]:
        """Get all connections (scoped to user if user_uuid is set)."""
        stmt = (
            select(GitHubConnection)
            .offset(offset)
            .limit(limit)
            .order_by(GitHubConnection.name)
        )
        stmt = self._apply_user_filter(stmt)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_by_repo(self, repo: str) -> Optional[GitHubConnection]:
        """Get a connection by repository name (owner/repo) (scoped to user if user_uuid is set)."""
        stmt = (
            select(GitHubConnection)
            .where(GitHubConnection.repo == repo)
        )
        stmt = self._apply_user_filter(stmt)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> Optional[GitHubConnection]:
        """Get a connection by its display name (scoped to user if user_uuid is set)."""
        stmt = (
            select(GitHubConnection)
            .where(GitHubConnection.name == name)
        )
        stmt = self._apply_user_filter(stmt)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_valid_connections(self) -> Sequence[GitHubConnection]:
        """Get all connections that have been verified as valid (scoped to user if user_uuid is set)."""
        stmt = (
            select(GitHubConnection)
            .where(GitHubConnection.is_valid == True)
            .order_by(GitHubConnection.name)
        )
        stmt = self._apply_user_filter(stmt)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update_test_status(
        self,
        id: str,
        is_valid: bool,
        error: Optional[str] = None
    ) -> Optional[GitHubConnection]:
        """Update the test status of a connection."""
        connection = await self.get_by_id(id)
        if connection:
            connection.is_valid = is_valid
            connection.last_tested_at = datetime.utcnow()
            connection.last_error = error
            await self.session.commit()
            await self.session.refresh(connection)
            return connection
        return None

    async def delete(self, id: str) -> bool:
        """Permanently delete a connection from the database."""
        connection = await self.get_by_id(id)
        if connection:
            await self.session.delete(connection)
            await self.session.commit()
            return True
        return False

    async def update_token_ref(
        self,
        id: str,
        token_ref: str,
    ) -> Optional[GitHubConnection]:
        """Update the env-token reference marker for a connection."""
        connection = await self.get_by_id(id)
        if connection:
            connection.token_ref = token_ref
            connection.is_valid = True
            connection.last_tested_at = None
            connection.last_error = None
            await self.session.commit()
            await self.session.refresh(connection)
            return connection
        return None
