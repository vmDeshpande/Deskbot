"""Vosk STT provider implementation."""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from app.config import Settings
from app.services.speech.base import STTProvider
from app.services.speech.models import (
    PartialTranscriptionResult,
    STTProviderInfo,
    TranscriptionResult,
)

logger = logging.getLogger(__name__)

# Lazy Vosk import
try:
    from vosk import KaldiRecognizer, Model

    _VOSK_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on environment
    Model = None  # type: ignore[assignment, misc]
    KaldiRecognizer = None  # type: ignore[assignment, misc]
    _VOSK_AVAILABLE = False


class VoskProvider(STTProvider):
    """Vosk-based speech recognition provider."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model: Any = None
        self._recognizer: KaldiRecognizer | None = None
        self._sample_rate: int = settings.audio_sample_rate

    @property
    def info(self) -> STTProviderInfo:
        return STTProviderInfo(
            name="vosk",
            model=self._settings.vosk_model_path or "not configured",
            supported_languages=["en"],  # Vosk models are typically single-language
            streaming=True,
        )

    async def initialize(self) -> None:
        if not _VOSK_AVAILABLE:
            logger.warning("Vosk package not available")
            return
        model_path = self._settings.vosk_model_path
        if not model_path:
            logger.warning("Vosk model path not configured")
            return
        try:
            self._model = Model(model_path)
            self._recognizer = KaldiRecognizer(self._model, self._sample_rate)
            logger.info("Vosk model loaded from %s", model_path)
        except Exception as exc:
            logger.warning("Failed to load Vosk model: %s", exc)
            self._model = None
            self._recognizer = None

    def _create_recognizer(self) -> KaldiRecognizer | None:
        """Create a new recognizer instance for a recording session."""
        if self._model is None:
            return None
        try:
            return KaldiRecognizer(self._model, self._sample_rate)
        except Exception as exc:
            logger.warning("Failed to create Vosk recognizer: %s", exc)
            return None

    async def transcribe(
        self,
        audio_data: bytes,
        sample_rate: int,
        language: str | None = None,
    ) -> TranscriptionResult:
        """Transcribe complete audio data (non-streaming)."""
        recognizer = self._create_recognizer()
        if recognizer is None:
            return TranscriptionResult(
                text="",
                language=language or "en",
                language_probability=None,
                metadata={"error": "Vosk not initialized"},
            )

        try:
            recognizer.AcceptWaveform(audio_data)
            result = json.loads(recognizer.Result())
            text = result.get("text", "")
            return TranscriptionResult(
                text=text,
                language=language or "en",
                language_probability=None,  # Vosk doesn't provide language confidence
                metadata={},
            )
        except Exception as exc:
            logger.warning("Vosk transcription failed: %s", exc)
            return TranscriptionResult(
                text="",
                language=language or "en",
                language_probability=None,
                metadata={"error": str(exc)},
            )

    async def transcribe_streaming(
        self,
        audio_chunks: AsyncIterator[bytes],
        sample_rate: int,
        language: str | None = None,
    ) -> AsyncIterator[PartialTranscriptionResult]:
        """Transcribe streaming audio chunks."""
        recognizer = self._create_recognizer()
        if recognizer is None:
            yield PartialTranscriptionResult(text="", is_final=True)
            return

        async for chunk in audio_chunks:
            try:
                is_final = recognizer.AcceptWaveform(chunk)
                if is_final:
                    result = json.loads(recognizer.Result())
                    text = result.get("text", "")
                    if text:
                        yield PartialTranscriptionResult(text=text, is_final=True)
                else:
                    partial = json.loads(recognizer.PartialResult())
                    partial_text = partial.get("partial", "")
                    if partial_text:
                        yield PartialTranscriptionResult(text=partial_text, is_final=False)
            except Exception as exc:
                logger.warning("Vosk streaming transcription error: %s", exc)

        # Final result
        try:
            final_result = json.loads(recognizer.FinalResult())
            text = final_result.get("text", "")
            if text:
                yield PartialTranscriptionResult(text=text, is_final=True)
        except Exception as exc:
            logger.warning("Vosk final transcription error: %s", exc)

    async def shutdown(self) -> None:
        self._model = None
        self._recognizer = None