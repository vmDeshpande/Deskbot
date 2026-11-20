"""Application settings loaded from environment variables and server/.env."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the Deskbot API."""

    service_name: str = "Deskbot API"
    version: str = "1.0.0"
    environment: str = "development"

    model_config = SettingsConfigDict(
        env_prefix="DESKBOT_",
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""

    return Settings()
