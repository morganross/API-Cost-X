"""GitHub token helpers for self-hosted root .env secrets."""
from app.config import get_settings


GITHUB_TOKEN_MESSAGE = "Set GITHUB_TOKEN in the root .env file."
GITHUB_TOKEN_REF = "env:GITHUB_TOKEN"


class GitHubTokenError(ValueError):
    """Raised when GITHUB_TOKEN is not configured."""


def get_github_token() -> str:
    """Return the GitHub token from the root .env file."""
    token = get_settings().github_token
    if not token:
        raise GitHubTokenError(GITHUB_TOKEN_MESSAGE)
    return token


def is_github_token_configured() -> bool:
    """GitHub features are available only when root .env has GITHUB_TOKEN."""
    return bool(get_settings().github_token)
