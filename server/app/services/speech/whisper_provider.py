"""Faster-Whisper STT provider implementation for multilingual recognition."""

from __future__ import annotations

import logging
import os
from typing import AsyncIterator

import numpy as np

from app.config import Settings
from app.services.speech.base import STTProvider
from app.services.speech.models import (
    PartialTranscriptionResult,
    STTProviderInfo,
    TranscriptionResult,
)

logger = logging.getLogger(__name__)

# Lazy faster-whisper import
try:
    from faster_whisper import WhisperModel

    _WHISPER_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on environment
    WhisperModel = None  # type: ignore[assignment, misc]
    _WHISPER_AVAILABLE = False


# Language code mapping: Whisper codes -> our normalized codes
WHISPER_LANGUAGE_MAP = {
    "en": "en",
    "hi": "hi",
    "mr": "mr",
    "english": "en",
    "hindi": "hi",
    "marathi": "mr",
}


class WhisperProvider(STTProvider):
    """Faster-Whisper based multilingual speech recognition provider."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model: WhisperModel | None = None
        self._model_size = settings.whisper_model
        self._device = settings.whisper_device
        self._compute_type = settings.whisper_compute_type
        self._supported_languages = [lang.strip() for lang in settings.supported_languages.split(",")]
        self._no_speech_threshold = settings.whisper_no_speech_threshold
        self._log_prob_threshold = settings.whisper_log_prob_threshold
        self._compression_ratio_threshold = settings.whisper_compression_ratio_threshold
        self._vad_filter = settings.whisper_vad_filter
        self._vad_parameters = settings.whisper_vad_parameters

    @property
    def info(self) -> STTProviderInfo:
        return STTProviderInfo(
            name="whisper",
            model=self._model_size,
            supported_languages=self._supported_languages,
            streaming=False,  # Whisper processes complete audio
        )

    async def initialize(self) -> None:
        if not _WHISPER_AVAILABLE:
            logger.warning("faster-whisper package not available")
            return

        # Determine device
        device = self._device
        if device == "cuda":
            # Check if CUDA is actually available
            try:
                import torch
                if not torch.cuda.is_available():
                    logger.warning("CUDA requested but not available, falling back to CPU")
                    device = "cpu"
            except ImportError:
                logger.warning("PyTorch not available, falling back to CPU")
                device = "cpu"

        # Validate compute type for device
        compute_type = self._compute_type
        if device == "cpu" and compute_type in ("float16", "float32"):
            logger.info("Using int8 compute type for CPU")
            compute_type = "int8"

        # Model path - use cache directory or download
        model_path = self._model_size

        try:
            logger.info(
                "Loading Whisper model: %s (device=%s, compute_type=%s)",
                model_path,
                device,
                compute_type,
            )
            self._model = WhisperModel(
                model_path,
                device=device,
                compute_type=compute_type,
                download_root=os.environ.get("WHISPER_MODEL_DIR"),
            )
            logger.info("Whisper model loaded successfully")
        except Exception as exc:
            logger.error("Failed to load Whisper model: %s", exc)
            self._model = None

    def _normalize_language(self, language: str | None) -> str | None:
        """Normalize Whisper language code to our standard format."""
        if not language:
            return None
        lang = language.lower().strip()
        return WHISPER_LANGUAGE_MAP.get(lang, lang.split("-")[0])

    def _is_supported_language(self, language: str | None) -> bool:
        """Check if detected language is in our supported list."""
        if not language:
            return False
        normalized = self._normalize_language(language)
        return normalized in self._supported_languages

    def _pcm_to_float32(self, pcm_data: bytes) -> np.ndarray:
        """Convert PCM16 bytes to float32 numpy array normalized to [-1, 1]."""
        # Convert bytes to int16 array
        audio_int16 = np.frombuffer(pcm_data, dtype=np.int16)
        # Normalize to float32 [-1, 1]
        audio_float32 = audio_int16.astype(np.float32) / 32768.0
        return audio_float32

    def _calculate_audio_duration(self, audio_data: bytes, sample_rate: int) -> float:
        """Calculate audio duration in seconds."""
        if not audio_data:
            return 0.0
        # PCM16: 2 bytes per sample
        num_samples = len(audio_data) // 2
        return num_samples / sample_rate

    async def transcribe(
        self,
        audio_data: bytes,
        sample_rate: int,
        language: str | None = None,
    ) -> TranscriptionResult:
        """Transcribe complete audio data using Whisper."""
        if self._model is None:
            return TranscriptionResult(
                text="",
                language=language or self._supported_languages[0] if self._supported_languages else "en",
                language_probability=None,
                metadata={"error": "Whisper model not initialized"},
            )

        if not audio_data:
            return TranscriptionResult(
                text="",
                language=language or self._supported_languages[0] if self._supported_languages else "en",
                language_probability=None,
                metadata={},
            )

        # Check minimum audio duration
        duration = self._calculate_audio_duration(audio_data, sample_rate)
        if duration < self._settings.audio_min_duration_s:
            logger.warning(
                "Audio too short: %.2fs < %.2fs minimum, skipping transcription",
                duration,
                self._settings.audio_min_duration_s,
            )
            return TranscriptionResult(
                text="",
                language=language or self._supported_languages[0] if self._supported_languages else "en",
                language_probability=None,
                metadata={"skipped": "audio_too_short", "duration": duration},
            )

        try:
            # Convert PCM16 to float32
            audio_array = self._pcm_to_float32(audio_data)

            # Prepare transcription options
            transcribe_options = {
                "language": language,
                "task": "transcribe",
                "beam_size": 5,
                "best_of": 5,
                "temperature": 0.0,
                "condition_on_previous_text": False,
                "no_speech_threshold": self._no_speech_threshold,
                "compression_ratio_threshold": self._compression_ratio_threshold,
                "log_prob_threshold": self._log_prob_threshold,
            }

            if self._vad_filter:
                transcribe_options["vad_filter"] = True
                transcribe_options["vad_parameters"] = self._vad_parameters

            # Run transcription
            segments, info = self._model.transcribe(audio_array, **transcribe_options)

            # Collect all segments
            text_parts = []
            for segment in segments:
                text_parts.append(segment.text.strip())

            full_text = " ".join(text_parts).strip()

            # Check for no speech detected via segment properties
            if not full_text and hasattr(info, 'no_speech_prob') and info.no_speech_prob > self._no_speech_threshold:
                logger.info("No speech detected (prob=%.2f)", info.no_speech_prob)
                return TranscriptionResult(
                    text="",
                    language=language or self._supported_languages[0] if self._supported_languages else "en",
                    language_probability=None,
                    metadata={"skipped": "no_speech", "no_speech_prob": info.no_speech_prob},
                )

            # Normalize detected language
            detected_lang = self._normalize_language(info.language)
            language_prob = info.language_probability

            # If detected language is not supported, use fallback
            if not self._is_supported_language(detected_lang):
                logger.warning(
                    "Detected language '%s' not in supported languages %s, using fallback",
                    detected_lang,
                    self._supported_languages,
                )
                detected_lang = self._supported_languages[0] if self._supported_languages else "en"

            # Check log probability for quality
            if hasattr(info, 'avg_logprob') and info.avg_logprob < self._log_prob_threshold:
                logger.warning(
                    "Low confidence transcription (avg_logprob=%.2f < %.2f): '%s'",
                    info.avg_logprob,
                    self._log_prob_threshold,
                    full_text,
                )

            return TranscriptionResult(
                text=full_text,
                language=detected_lang,
                language_probability=language_prob,
                metadata={
                    "whisper_language": info.language,
                    "whisper_language_probability": info.language_probability,
                    "duration": info.duration,
                    "all_language_probs": getattr(info, "all_language_probs", None),
                    "avg_logprob": getattr(info, "avg_logprob", None),
                    "no_speech_prob": getattr(info, "no_speech_prob", None),
                    "compression_ratio": getattr(info, "compression_ratio", None),
                },
            )

        except Exception as exc:
            logger.error("Whisper transcription failed: %s", exc)
            return TranscriptionResult(
                text="",
                language=language or self._supported_languages[0] if self._supported_languages else "en",
                language_probability=None,
                metadata={"error": str(exc)},
            )

    async def transcribe_streaming(
        self,
        audio_chunks: AsyncIterator[bytes],
        sample_rate: int,
        language: str | None = None,
    ) -> AsyncIterator[PartialTranscriptionResult]:
        """Whisper doesn't support true streaming; collect all chunks and transcribe once."""
        # Collect all chunks
        all_chunks = []
        async for chunk in audio_chunks:
            all_chunks.append(chunk)

        if not all_chunks:
            yield PartialTranscriptionResult(text="", is_final=True)
            return

        # Combine and transcribe
        combined_audio = b"".join(all_chunks)
        result = await self.transcribe(combined_audio, sample_rate, language)

        if result.text:
            yield PartialTranscriptionResult(text=result.text, is_final=True)
        else:
            yield PartialTranscriptionResult(text="", is_final=True)

    async def shutdown(self) -> None:
        self._model = None