import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

os.environ["DEBUG"] = "false"

from app.api.routes import provider_keys
from app.api.routes.runs import artifacts
from app.main import create_app
from app.services import export_service


@pytest.mark.asyncio
async def test_generic_exception_handler_hides_exception_message():
    app = create_app()

    @app.get("/__test__/boom")
    async def boom():
        raise RuntimeError("secret token from /tmp/private.txt")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/__test__/boom")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert "secret token" not in response.text


class _FakeRunRepository:
    def __init__(self, db, user_uuid):
        self.db = db
        self.user_uuid = user_uuid

    async def get_by_id(self, run_id):
        return SimpleNamespace(title="My Run", status="completed")

    async def get_with_tasks(self, run_id):
        return SimpleNamespace(title="My Run", status="completed")


@pytest.mark.asyncio
async def test_generate_run_export_hides_internal_error(monkeypatch):
    async def failing_build_run_export(**kwargs):
        raise RuntimeError("secret export failure at /tmp/private.zip")

    monkeypatch.setattr(artifacts, "RunRepository", _FakeRunRepository)
    monkeypatch.setattr(export_service, "build_run_export", failing_build_run_export)

    with pytest.raises(HTTPException) as excinfo:
        await artifacts.generate_run_export("run-123", user={"uuid": "user-123"}, db=object())

    assert excinfo.value.status_code == 500
    assert excinfo.value.detail == "Export generation failed"
    assert "secret export failure" not in excinfo.value.detail


@pytest.mark.asyncio
async def test_download_run_export_hides_internal_error(monkeypatch, tmp_path):
    async def failing_build_run_export(**kwargs):
        raise RuntimeError("secret export failure at /tmp/private.zip")

    monkeypatch.setattr(artifacts, "RunRepository", _FakeRunRepository)
    monkeypatch.setattr(export_service, "build_run_export", failing_build_run_export)
    monkeypatch.setattr(artifacts, "get_run_root", lambda user_uuid, run_id: tmp_path / "runs" / run_id)

    with pytest.raises(HTTPException) as excinfo:
        await artifacts.download_run_export("run-123", user={"uuid": "user-123"}, db=object())

    assert excinfo.value.status_code == 500
    assert excinfo.value.detail == "Export generation failed"
    assert "secret export failure" not in excinfo.value.detail


@pytest.mark.asyncio
async def test_get_generated_doc_content_hides_internal_error(monkeypatch, tmp_path):
    run_root = tmp_path / "runs" / "run-123"
    generated_dir = run_root / "generated"
    generated_dir.mkdir(parents=True)
    (generated_dir / "doc-1.md").write_text("placeholder", encoding="utf-8")

    def failing_read_text(self, encoding="utf-8"):
        raise RuntimeError("secret file path /tmp/private.md")

    monkeypatch.setattr(artifacts, "RunRepository", _FakeRunRepository)
    monkeypatch.setattr(artifacts, "get_run_root", lambda user_uuid, run_id: run_root)
    monkeypatch.setattr(Path, "read_text", failing_read_text)

    with pytest.raises(HTTPException) as excinfo:
        await artifacts.get_generated_doc_content(
            "run-123",
            "doc-1",
            user={"uuid": "user-123"},
            db=object(),
        )

    assert excinfo.value.status_code == 500
    assert excinfo.value.detail == "Failed to read document"
    assert "secret file path" not in excinfo.value.detail


@pytest.mark.asyncio
async def test_save_provider_key_hides_validation_error(monkeypatch):
    class FakeKeyManager:
        async def save_key(self, provider, api_key):
            raise ValueError("secret config error")

    monkeypatch.setattr(provider_keys, "get_provider_key_manager", lambda db, user_uuid: FakeKeyManager())

    with pytest.raises(HTTPException) as excinfo:
        await provider_keys.save_provider_key(
            provider_keys.ProviderKeyCreate(provider="openai", api_key="sk-test"),
            user={"uuid": "user-123"},
            db=object(),
        )

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "Invalid provider API key data"
    assert "secret config error" not in excinfo.value.detail
