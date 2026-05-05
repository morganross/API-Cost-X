"""Provider key injection from the single root .env file."""
import logging
from typing import Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

PROVIDER_TO_ENV_VAR = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "openaidp": "OPENAI_API_KEY",
    "googledp": "GOOGLE_API_KEY",
}


def _root_env_keys() -> Dict[str, Optional[str]]:
    from app.config import get_settings

    settings = get_settings()
    return {
        "OPENAI_API_KEY": settings.openai_api_key,
        "ANTHROPIC_API_KEY": settings.anthropic_api_key,
        "GOOGLE_API_KEY": settings.google_api_key,
        "PERPLEXITY_API_KEY": settings.perplexity_api_key,
        "OPENROUTER_API_KEY": settings.openrouter_api_key,
        "GROQ_API_KEY": settings.groq_api_key,
        "TAVILY_API_KEY": settings.tavily_api_key,
        "GITHUB_TOKEN": settings.github_token,
    }


def _inject_env_file_keys(env: Dict[str, str], user_uuid: str = "local") -> bool:
    injected = 0
    for env_var, value in _root_env_keys().items():
        if value:
            env[env_var] = value
            injected += 1
    if injected:
        logger.debug("[KEY_INJECTION] Injected %d root .env key(s)", injected)
    return injected > 0


async def inject_provider_keys_for_user(
    session: AsyncSession,
    user_uuid: str,
    env: Dict[str, str],
    key_mode: str,
) -> Dict[str, str]:
    """Compatibility wrapper: inject provider keys from root .env only."""
    _inject_env_file_keys(env, user_uuid)
    return env


async def inject_provider_keys_for_user_auto(
    user_uuid: str,
    env: Dict[str, str],
    key_mode: str,
) -> Dict[str, str]:
    """Inject provider keys from root .env only."""
    _inject_env_file_keys(env, user_uuid)
    return env


async def get_provider_key(
    session: AsyncSession,
    user_uuid: str,
    provider: str,
) -> Optional[str]:
    """Return a provider key from root .env."""
    env_var = PROVIDER_TO_ENV_VAR.get(provider.lower())
    if not env_var:
        return None
    return _root_env_keys().get(env_var)


async def get_provider_key_auto(user_uuid: str, provider: str) -> Optional[str]:
    """Return a provider key from root .env."""
    return await get_provider_key(None, user_uuid, provider)  # type: ignore[arg-type]
