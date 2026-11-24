"""Base STT provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from app.services.speech.models import (
    PartialTranscriptionResult,
    STTProviderInfo,
    TranscriptionResult,
)


class STTProvider(ABC):
    """Abstract base class for speech-to-text providers."""

    @property
    @abstractmethod
    def info(self) -> STTProviderInfo:
        """Return provider information."""

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the provider (load model, etc.)."""

    @abstractmethod
    async def transcribe(
        self,
        audio_data: bytes,
        sample_rate: int,
        language: str | None = None,
    ) -> TranscriptionResult:
        """Transcribe complete audio data."""

    @abstractmethod
    async def transcribe_streaming(
        self,
        audio_chunks: AsyncIterator[bytes],
        sample_rate: int,
        language: str | None = None,
    ) -> AsyncIterator[PartialTranscriptionResult]:
        """Transcribe streaming audio chunks."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Clean up resources."""

    def normalize_language(self, language: str | None) -> str | None:
        """Normalize language code to standard format."""
        if not language:
            return None
        lang = language.lower().strip()
        # Map common variants
        mapping = {
            "en-us": "en",
            "en-gb": "en",
            "english": "en",
            "hi-in": "hi",
            "hindi": "hi",
            "mr-in": "mr",
            "marathi": "mr",
        }
        return mapping.get(lang, lang.split("-")[0])