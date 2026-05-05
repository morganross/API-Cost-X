"""
Application configuration for the self-hosted app.

All runtime configuration and optional provider secrets come from the root
.env file. The default database is a plain local SQLite file.
"""
from functools import lru_cache
import os
from pathlib import Path
from typing import Optional

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from the root .env file or environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "API Cost X"
    app_version: str = "2.0.0"
    debug: bool = False
    apicostx_log_level: str = Field(default="INFO", validation_alias="API_COST_X_LOG_LEVEL")

    @field_validator("debug", mode="before")
    @classmethod
    def _parse_debug(cls, value):
        """Accept standard booleans plus common deployment aliases."""
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            truthy = {"1", "true", "t", "yes", "y", "on"}
            falsy = {"0", "false", "f", "no", "n", "off", "release", "prod", "production", "live"}
            if normalized in truthy:
                return True
            if normalized in falsy:
                return False
            raise ValueError("debug must be a boolean or a recognized deployment value")
        return value

    # Database: plain SQLite only.
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/api-cost-x.db",
        validation_alias=AliasChoices("DATABASE_URL", "API_COST_X_DATABASE_URL"),
    )

    # Optional provider/API secrets. These are never stored in SQLite.
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    google_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None
    perplexity_api_key: Optional[str] = None
    tavily_api_key: Optional[str] = None
    github_token: Optional[str] = None

    # Paths
    data_dir: Path = Field(default=Path("./data"), validation_alias=AliasChoices("DATA_DIR", "API_COST_X_DATA_DIR"))
    documents_dir: Path = Field(default=Path("./data/documents"), validation_alias="DOCUMENTS_DIR")
    artifacts_dir: Path = Field(default=Path("./data/artifacts"), validation_alias="ARTIFACTS_DIR")
    logs_dir: Path = Field(default=Path("./logs"), validation_alias="LOGS_DIR")

    def model_post_init(self, __context):
        default_data_dir = Path("./data")
        if self.data_dir != default_data_dir:
            if self.documents_dir == default_data_dir / "documents":
                self.documents_dir = self.data_dir / "documents"
            if self.artifacts_dir == default_data_dir / "artifacts":
                self.artifacts_dir = self.data_dir / "artifacts"

    # Execution
    max_concurrent_tasks: int = 3
    safety_ceiling_seconds: int = 86400  # 24 hours

    # CORS
    cors_origins: list[str] = []
    cors_dev_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://localhost",
        "http://127.0.0.1",
        "http://localhost:80",
        "http://127.0.0.1:80",
    ]
    cors_allow_credentials: bool = False

    @property
    def resolved_cors_origins(self) -> list[str]:
        """Return the effective CORS allowlist for the local web GUI."""
        # The self-hosted web GUI and API service run on different local ports,
        # so localhost origins must be allowed even when debug mode is off.
        origins = list(self.cors_dev_origins)
        origins.extend(self.cors_origins)

        extra_origins = os.getenv("API_COST_X_CORS_ORIGINS", "")
        if extra_origins:
            origins.extend([origin.strip() for origin in extra_origins.split(",") if origin.strip()])

        return list(dict.fromkeys(origins))

    def ensure_dirs(self) -> None:
        """Create required local directories if they don't exist."""
        for dir_path in [self.data_dir, self.documents_dir, self.artifacts_dir, self.logs_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    settings = Settings()
    settings.ensure_dirs()
    return settings
