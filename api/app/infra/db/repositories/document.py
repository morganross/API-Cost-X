"""
Document repository for CRUD operations on documents.
"""
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models.document import Document
from app.infra.db.repositories.base import BaseRepository


class DocumentRepository(BaseRepository[Document]):
    """Repository for Document CRUD operations."""

    def __init__(self, session: AsyncSession, user_uuid: Optional[str] = None):
        super().__init__(Document, session, user_uuid)

    async def get_by_path(self, path: str) -> Optional[Document]:
        """Get a document by its file path (scoped to user if user_uuid is set)."""
        stmt = select(Document).where(Document.path == path)
        stmt = self._apply_user_filter(stmt)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> Optional[Document]:
        """Get a document by its exact name (scoped to user if user_uuid is set)."""
        stmt = select(Document).where(Document.name == name)
        stmt = self._apply_user_filter(stmt)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active(self, limit: int = 100, offset: int = 0) -> Sequence[Document]:
        """Get all documents (scoped to user if user_uuid is set)."""
        stmt = (
            select(Document)
            .offset(offset)
            .limit(limit)
            .order_by(Document.created_at.desc())
        )
        stmt = self._apply_user_filter(stmt)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_by_file_type(self, file_type: str) -> Sequence[Document]:
        """Get all documents of a specific file type (scoped to user if user_uuid is set)."""
        stmt = (
            select(Document)
            .where(Document.file_type == file_type)
            .order_by(Document.name)
        )
        stmt = self._apply_user_filter(stmt)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def search_by_name(self, query: str, limit: int = 50) -> Sequence[Document]:
        """Search documents by name (case-insensitive contains, scoped to user if user_uuid is set)."""
        stmt = (
            select(Document)
            .where(Document.name.ilike(f"%{query}%"))
            .limit(limit)
            .order_by(Document.name)
        )
        stmt = self._apply_user_filter(stmt)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def delete(self, id: str) -> bool:
        """Permanently delete a document from the database."""
        doc = await self.get_by_id(id)
        if doc:
            await self.session.delete(doc)
            await self.session.commit()
            return True
        return False

    async def get_by_ids(self, ids: list[str]) -> Sequence[Document]:
        """Get multiple documents by their IDs (scoped to user if user_uuid is set)."""
        if not ids:
            return []
        stmt = select(Document).where(Document.id.in_(ids))
        stmt = self._apply_user_filter(stmt)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update_content(
        self,
        id: str,
        content: str,
        content_hash: str,
        size_bytes: int,
        word_count: int
    ) -> Optional[Document]:
        """Update document content and metadata."""
        doc = await self.get_by_id(id)
        if doc:
            doc.content = content
            doc.content_hash = content_hash
            doc.size_bytes = size_bytes
            doc.word_count = word_count
            await self.session.commit()
            await self.session.refresh(doc)
            return doc
        return None
