"""
Settings API Routes.

Stores arbitrary local settings as JSON in the user's database.
"""
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.middleware import get_current_user
from app.infra.db.models.user_settings import UserSettings
from app.infra.db.session import get_user_db

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsPayload(BaseModel):
    """Arbitrary settings payload."""

    model_config = ConfigDict(extra="allow")


@router.get("")
async def get_settings(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> Dict[str, Any]:
    """Get stored settings for the current user."""
    result = await db.execute(
        select(UserSettings).where(UserSettings.user_uuid == user["uuid"])
    )
    settings_row = result.scalar_one_or_none()
    if not settings_row:
        return {}
    return settings_row.settings


@router.put("")
async def update_settings(
    payload: SettingsPayload,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> Dict[str, Any]:
    """Update stored settings for the current user."""
    settings_data = payload.model_dump()

    result = await db.execute(
        select(UserSettings).where(UserSettings.user_uuid == user["uuid"])
    )
    settings_row = result.scalar_one_or_none()

    if settings_row:
        settings_row.settings = settings_data
        settings_row.updated_at = datetime.utcnow()
    else:
        settings_row = UserSettings(
            user_uuid=user["uuid"],
            settings=settings_data,
            updated_at=datetime.utcnow(),
        )
        db.add(settings_row)

    return settings_row.settings
