"""Application settings loaded from environment variables and server/.env."""

from functools import lru_cache
from pathlib import Path
from uuid import UUID

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the Deskbot API."""

    service_name: str = "Deskbot API"
    version: str = "1.0.0"
    environment: str = "development"
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    websocket_path: str = "/ws/device"
    device_token_hashes: dict[str, str] = Field(default_factory=dict, repr=False)
    websocket_max_message_bytes: int = Field(default=4096, ge=256, le=65536)
    websocket_outbound_queue_size: int = Field(default=32, ge=1, le=256)
    websocket_send_timeout_s: float = Field(default=5.0, gt=0, le=60)
    device_registration_timeout_s: float = Field(default=10.0, gt=0, le=120)
    device_heartbeat_interval_s: int = Field(default=30, ge=1, le=300)
    device_heartbeat_timeout_s: int = Field(default=90, ge=2, le=900)
    websocket_max_messages_per_minute: int = Field(default=120, ge=1, le=10000)
    websocket_max_protocol_strikes: int = Field(default=3, ge=1, le=20)

    model_config = SettingsConfigDict(
        env_prefix="DESKBOT_",
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("host must not be empty")
        return value

    @field_validator("websocket_path")
    @classmethod
    def validate_websocket_path(cls, value: str) -> str:
        if not value.startswith("/") or value.startswith("//") or "?" in value or "#" in value:
            raise ValueError("websocket_path must be an absolute URL path without query or fragment")
        return value

    @field_validator("device_token_hashes")
    @classmethod
    def validate_device_token_hashes(cls, values: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for device_id, token_hash in values.items():
            canonical_id = str(UUID(device_id))
            if len(token_hash) != 64 or any(char not in "0123456789abcdefABCDEF" for char in token_hash):
                raise ValueError("device token verifiers must be 64-character SHA-256 hex digests")
            if canonical_id in normalized:
                raise ValueError("duplicate device UUID in device token configuration")
            if token_hash.lower() in normalized.values():
                raise ValueError("a device token verifier must be bound to only one UUID")
            normalized[canonical_id] = token_hash.lower()
        return normalized


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""

    return Settings()
