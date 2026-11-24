"""Base TTS provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.services.tts.models import (
    TTSProviderInfo,
    TTSResult,
    TTSError,
)


class TTSProvider(ABC):
    """Abstract base class for text-to-speech providers."""

    @property
    @abstractmethod
    def info(self) -> TTSProviderInfo:
        """Return provider information."""

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the provider (load config, verify API access, etc.)."""

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        language: str,
    ) -> TTSResult:
        """
        Synthesize text to speech.

        Args:
            text: Text to synthesize
            language: BCP-47 language code (e.g., "en-IN", "hi-IN", "mr-IN")

        Returns:
            TTSResult with PCM audio data and metadata
        """

    @abstractmethod
    async def shutdown(self) -> None:
        """Clean up resources."""

    def get_voice_for_language(self, language: str) -> str:
        """
        Get the voice name for a given language.
        Override in subclasses to provide custom voice mapping.
        """
        return ""