"""Resolve provider-key availability for local self-hosted runs."""
from dataclasses import dataclass
import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KeyModeResolution:
    """Resolved key-source state for a local run."""

    key_mode: str
    has_provider_keys: bool


def resolve_key_mode_from_state(
    has_byok: bool,
    use_byok_first: bool = True,
) -> str:
    """Hosted billing modes are disabled; local runs use root .env keys."""
    return "byok"


async def resolve_effective_key_mode(
    session: AsyncSession,
    user_uuid: str,
    use_byok_first: bool = True,
) -> KeyModeResolution:
    """Resolve provider-key mode from the single root .env file."""
    from app.security.key_injection import _root_env_keys

    has_keys = any(value for value in _root_env_keys().values())
    logger.info(
        "[KEY_MODE] local self-hosted run: root_env_keys=%s resolved=byok",
        has_keys,
    )
    return KeyModeResolution(key_mode="byok", has_provider_keys=has_keys)
