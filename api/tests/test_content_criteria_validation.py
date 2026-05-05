from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.api.routes import contents
from app.api.schemas.content import ContentCreate, ContentType, ContentUpdate


def _content_repo_factory(content=None, capture=None):
    class _ContentRepo:
        def __init__(self, db, user_uuid=None):
            self.db = db
            self.user_uuid = user_uuid

        async def get_by_name(self, name):
            return None

        async def get_by_id(self, content_id):
            return content

        async def create(self, **kwargs):
            if capture is not None:
                capture.setdefault("create_calls", []).append(kwargs)
            return SimpleNamespace(
                id="content-1",
                name=kwargs["name"],
                content_type=kwargs["content_type"],
                body=kwargs["body"],
                variables=kwargs.get("variables") or {},
                description=kwargs.get("description"),
                tags=kwargs.get("tags") or [],
                created_at=datetime.now(timezone.utc),
                updated_at=None,
            )

        async def update(self, content_id, **kwargs):
            if capture is not None:
                capture.setdefault("update_calls", []).append(kwargs)
            for key, value in kwargs.items():
                setattr(content, key, value)
            return content

    return _ContentRepo


@pytest.mark.asyncio
async def test_create_content_rejects_invalid_eval_criteria(monkeypatch):
    capture = {}
    monkeypatch.setattr(contents, "ContentRepository", _content_repo_factory(capture=capture))

    with pytest.raises(Exception) as exc_info:
        await contents.create_content(
            ContentCreate(
                name="Bad criteria",
                content_type=ContentType.EVAL_CRITERIA,
                body="criteria:\n  - clarity\n",
            ),
            user={"uuid": "user-1"},
            db=object(),
        )

    assert exc_info.value.status_code == 400
    assert capture.get("create_calls") is None


@pytest.mark.asyncio
async def test_update_content_rejects_invalid_eval_criteria(monkeypatch):
    existing = SimpleNamespace(
        id="content-1",
        name="Criteria",
        content_type=ContentType.EVAL_CRITERIA.value,
        body="criteria:\n  - name: clarity\n    description: Be clear.\n",
        variables={},
        description=None,
        tags=[],
        created_at=datetime.now(timezone.utc),
        updated_at=None,
    )
    capture = {}
    monkeypatch.setattr(contents, "ContentRepository", _content_repo_factory(existing, capture))

    with pytest.raises(Exception) as exc_info:
        await contents.update_content(
            "content-1",
            ContentUpdate(body="criteria:\n  - clarity\n"),
            user={"uuid": "user-1"},
            db=object(),
        )

    assert exc_info.value.status_code == 400
    assert capture.get("update_calls") is None
