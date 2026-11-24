"""Google Cloud Text-to-Speech provider implementation."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from google.cloud import texttospeech
from google.cloud.texttospeech_v1.types import AudioEncoding
from google.api_core.exceptions import GoogleAPICallError, RetryError

from app.config import Settings
from app.services.tts.base import TTSProvider
from app.services.tts.models import (
    TTSProviderInfo,
    TTSResult,
    TTSError,
    TTSTimeoutError,
    TTSAPIError,
    TTSInvalidResponseError,
    TTSCredentialsError,
)

logger = logging.getLogger(__name__)

# Voice mappings for supported languages
# Using Standard voices for reliability; can be upgraded to Neural/Wavenet if available
DEFAULT_VOICES = {
    "en-IN": "en-IN-Standard-B",  # English India - male voice
    "hi-IN": "hi-IN-Standard-A",  # Hindi India - female voice
    "mr-IN": "mr-IN-Standard-A",  # Marathi India - female voice
}

# Language code mapping for Google Cloud TTS
LANGUAGE_CODE_MAP = {
    "en": "en-IN",
    "en-IN": "en-IN",
    "hi": "hi-IN",
    "hi-IN": "hi-IN",
    "mr": "mr-IN",
    "mr-IN": "mr-IN",
}


class GoogleTTSProvider(TTSProvider):
    """Google Cloud Text-to-Speech provider."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: texttospeech.TextToSpeechAsyncClient | None = None
        self._timeout = 30.0  # seconds
        self._credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        self._voice_overrides = {
            "en-IN": settings.tts_voice_en or DEFAULT_VOICES["en-IN"],
            "hi-IN": settings.tts_voice_hi or DEFAULT_VOICES["hi-IN"],
            "mr-IN": settings.tts_voice_mr or DEFAULT_VOICES["mr-IN"],
        }

    @property
    def info(self) -> TTSProviderInfo:
        return TTSProviderInfo(
            name="google",
            model="google-cloud-tts",
            supported_languages=list(DEFAULT_VOICES.keys()),
            sample_rate=settings.tts_sample_rate,
            audio_format=settings.tts_audio_format,
        )

    async def initialize(self) -> None:
        """Initialize the Google Cloud TTS client."""
        if not self._credentials_path:
            # Check if credentials are available via ADC
            logger.info("No GOOGLE_APPLICATION_CREDENTIALS set, attempting Application Default Credentials")

        try:
            self._client = texttospeech.TextToSpeechAsyncClient()
            logger.info(
                "Google TTS provider initialized with voices: en-IN=%s, hi-IN=%s, mr-IN=%s",
                self._voice_overrides["en-IN"],
                self._voice_overrides["hi-IN"],
                self._voice_overrides["mr-IN"],
            )
        except Exception as exc:
            logger.error("Failed to initialize Google TTS provider: %s", exc)
            self._client = None
            raise TTSCredentialsError(f"Failed to initialize Google TTS: {exc}") from exc

    def get_voice_for_language(self, language: str) -> str:
        """Get the voice name for a given language."""
        normalized = LANGUAGE_CODE_MAP.get(language.lower(), language)
        return self._voice_overrides.get(normalized, DEFAULT_VOICES.get(normalized, ""))

    def _normalize_language(self, language: str) -> str:
        """Normalize language code to Google Cloud format."""
        return LANGUAGE_CODE_MAP.get(language.lower(), language)

    async def synthesize(self, text: str, language: str) -> TTSResult:
        """Synthesize text to speech using Google Cloud TTS."""
        if self._client is None:
            raise TTSError("Google TTS provider not initialized", code="NOT_INITIALIZED", retryable=False)

        if not text or not text.strip():
            logger.warning("Empty text provided to TTS, returning empty result")
            return TTSResult(
                audio_data=b"",
                sample_rate=self._settings.tts_sample_rate,
                channels=1,
                bits_per_sample=16,
                format="pcm_s16le",
                duration_seconds=0.0,
                metadata={"empty": True},
            )

        normalized_lang = self._normalize_language(language)
        voice_name = self.get_voice_for_language(normalized_lang)

        if not voice_name:
            logger.warning("No voice configured for language %s, using default", language)
            voice_name = DEFAULT_VOICES.get(normalized_lang, "en-IN-Standard-B")

        logger.info(
            "event=tts.synthesis_start language=%s voice=%s text_length=%d",
            normalized_lang,
            voice_name,
            len(text),
        )

        start_time = time.monotonic()

        try:
            # Build synthesis input
            synthesis_input = texttospeech.SynthesisInput(text=text)

            # Build voice selection
            voice = texttospeech.VoiceSelectionParams(
                language_code=normalized_lang,
                name=voice_name,
            )

            # Build audio config - request LINEAR16 (PCM) at 16kHz
            audio_config = texttospeech.AudioConfig(
                audio_encoding=AudioEncoding.LINEAR16,
                sample_rate_hertz=self._settings.tts_sample_rate,
            )

            # Call the API
            response = await self._client.synthesize_speech(
                input=synthesis_input,
                voice=voice,
                audio_config=audio_config,
            )

            elapsed = time.monotonic() - start_time
            audio_content = response.audio_content

            if not audio_content:
                raise TTSInvalidResponseError("Empty audio content returned from Google TTS")

            # Validate audio format (Google returns raw PCM for LINEAR16)
            # Sample rate is set in audio_config, channels=1 (mono), 16-bit signed
            duration = len(audio_content) / (self._settings.tts_sample_rate * 2)  # 2 bytes per sample (16-bit)

            logger.info(
                "event=tts.synthesis_completed language=%s voice=%s bytes=%d duration=%.2fs elapsed=%.2fs",
                normalized_lang,
                voice_name,
                len(audio_content),
                duration,
                elapsed,
            )

            return TTSResult(
                audio_data=audio_content,
                sample_rate=self._settings.tts_sample_rate,
                channels=1,
                bits_per_sample=16,
                format="pcm_s16le",
                duration_seconds=duration,
                metadata={
                    "voice": voice_name,
                    "language": normalized_lang,
                    "provider": "google",
                    "synthesis_time_seconds": elapsed,
                },
            )

        except GoogleAPICallError as exc:
            elapsed = time.monotonic() - start_time
            logger.error("Google TTS API error after %.2fs: %s", elapsed, exc)
            status_code = getattr(exc, "code", None)
            raise TTSAPIError(f"Google TTS API error: {exc}", status_code=status_code) from exc
        except RetryError as exc:
            elapsed = time.monotonic() - start_time
            logger.error("Google TTS retry error after %.2fs: %s", elapsed, exc)
            raise TTSTimeoutError(f"Google TTS request timed out: {exc}") from exc
        except Exception as exc:
            elapsed = time.monotonic() - start_time
            logger.error("Google TTS synthesis failed after %.2fs: %s", elapsed, exc)
            raise TTSAPIError(f"Google TTS synthesis failed: {exc}") from exc

    async def shutdown(self) -> None:
        """Clean up Google TTS client."""
        if self._client is not None:
            await self._client.close()
            self._client = None
        logger.info("Google TTS provider shut down")