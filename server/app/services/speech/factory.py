"""STT provider factory."""

from __future__ import annotations

import logging
from typing import Any

from app.config import Settings
from app.services.speech.base import STTProvider
from app.services.speech.models import STTProviderInfo
from app.services.speech.vosk_provider import VoskProvider
from app.services.speech.whisper_provider import WhisperProvider

logger = logging.getLogger(__name__)


class STTProviderFactory:
    """Factory for creating STT providers based on configuration."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._provider: STTProvider | None = None

    async def get_provider(self) -> STTProvider:
        """Get or create the configured STT provider."""
        if self._provider is not None:
            return self._provider

        provider_name = self._settings.stt_provider.lower()

        if provider_name == "whisper":
            logger.info("Creating Whisper STT provider")
            self._provider = WhisperProvider(self._settings)
        elif provider_name == "vosk":
            logger.info("Creating Vosk STT provider")
            self._provider = VoskProvider(self._settings)
        else:
            logger.warning(
                "Unknown STT provider '%s', falling back to Vosk",
                provider_name,
            )
            self._provider = VoskProvider(self._settings)

        await self._provider.initialize()
        return self._provider

    async def shutdown(self) -> None:
        """Shutdown the current provider."""
        if self._provider is not None:
            await self._provider.shutdown()
            self._provider = None

    def get_provider_info(self) -> STTProviderInfo | None:
        """Get info about the current provider without initializing."""
        if self._provider is not None:
            return self._provider.info
        return None