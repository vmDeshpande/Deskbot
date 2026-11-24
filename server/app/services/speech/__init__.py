"""Speech recognition package."""

from app.services.speech.base import STTProvider
from app.services.speech.factory import STTProviderFactory
from app.services.speech.models import (
    PartialTranscriptionResult,
    STTProviderInfo,
    TranscriptionResult,
)
from app.services.speech.vosk_provider import VoskProvider
from app.services.speech.whisper_provider import WhisperProvider

__all__ = [
    "STTProvider",
    "STTProviderFactory",
    "VoskProvider",
    "WhisperProvider",
    "TranscriptionResult",
    "PartialTranscriptionResult",
    "STTProviderInfo",
]