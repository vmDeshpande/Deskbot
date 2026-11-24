"""TTS service package."""

from __future__ import annotations

from app.services.tts.base import TTSProvider
from app.services.tts.factory import TTSProviderFactory
from app.services.tts.models import (
    TTSProviderInfo,
    TTSResult,
    TTSError,
    TTSTimeoutError,
    TTSAPIError,
    TTSInvalidResponseError,
    TTSCredentialsError,
)

__all__ = [
    "TTSProvider",
    "TTSProviderFactory",
    "TTSProviderInfo",
    "TTSResult",
    "TTSError",
    "TTSTimeoutError",
    "TTSAPIError",
    "TTSInvalidResponseError",
    "TTSCredentialsError",
]