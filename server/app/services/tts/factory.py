"""TTS provider factory."""

from __future__ import annotations

import logging

from app.config import Settings
from app.services.tts.base import TTSProvider
from app.services.tts.google_provider import GoogleTTSProvider
from app.services.tts.models import TTSError, TTSCredentialsError

logger = logging.getLogger(__name__)


class TTSProviderFactory:
    """Factory for creating and managing TTS providers."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._provider: TTSProvider | None = None
        self._initialized = False

    async def get_provider(self) -> TTSProvider | None:
        """Get the initialized TTS provider, creating it if necessary."""
        if not self._settings.tts_enabled:
            logger.info("TTS is disabled via configuration")
            return None

        if self._provider is not None:
            return self._provider

        provider_type = self._settings.tts_provider.lower()
        if provider_type == "google":
            self._provider = GoogleTTSProvider(self._settings)
        else:
            logger.warning("Unknown TTS provider: %s", provider_type)
            return None

        try:
            await self._provider.initialize()
            self._initialized = True
            logger.info("TTS provider initialized: %s", self._provider.info.name)
        except Exception as exc:
            logger.error("Failed to initialize TTS provider: %s", exc)
            self._provider = None
            self._initialized = False
            raise

        return self._provider

    async def shutdown(self) -> None:
        """Shutdown the TTS provider."""
        if self._provider is not None:
            await self._provider.shutdown()
            self._provider = None
            self._initialized = False
            logger.info("TTS provider shut down")

    def get_provider_info(self) -> dict[str, Any]:
        """Get information about the current provider."""
        if self._provider is not None and self._initialized:
            return {
                "name": self._provider.info.name,
                "model": self._provider.info.model,
                "supported_languages": self._provider.info.supported_languages,
                "sample_rate": self._provider.info.sample_rate,
                "audio_format": self._provider.info.audio_format,
                "enabled": True,
            }
        return {
            "enabled": self._settings.tts_enabled,
            "provider": self._settings.tts_provider,
            "initialized": self._initialized,
        }