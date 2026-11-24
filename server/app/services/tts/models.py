"""TTS provider models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TTSProviderInfo:
    """Information about a TTS provider."""

    name: str
    model: str
    supported_languages: list[str]
    sample_rate: int
    audio_format: str


@dataclass(frozen=True)
class TTSResult:
    """Result from TTS synthesis."""

    audio_data: bytes
    sample_rate: int
    channels: int
    bits_per_sample: int
    format: str
    duration_seconds: float
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class TTSError(Exception):
    """Base exception for TTS provider errors."""

    message: str
    code: str = "TTS_ERROR"
    retryable: bool = False

    def __init__(self, message: str, code: str = "TTS_ERROR", retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class TTSTimeoutError(TTSError):
    """Raised when TTS provider request times out."""

    def __init__(self, message: str = "TTS provider request timed out"):
        super().__init__(message, code="TIMEOUT", retryable=True)


@dataclass(frozen=True)
class TTSAPIError(TTSError):
    """Raised when TTS provider returns an API error."""

    status_code: int | None = None

    def __init__(self, message: str, status_code: int | None = None):
        retryable = status_code in (429, 500, 502, 503, 504) if status_code else False
        super().__init__(message, code="API_ERROR", retryable=retryable)
        self.status_code = status_code


@dataclass(frozen=True)
class TTSInvalidResponseError(TTSError):
    """Raised when TTS provider returns invalid/malformed response."""

    def __init__(self, message: str = "Invalid response from TTS provider"):
        super().__init__(message, code="INVALID_RESPONSE", retryable=False)


@dataclass(frozen=True)
class TTSCredentialsError(TTSError):
    """Raised when TTS credentials are missing or invalid."""

    def __init__(self, message: str = "TTS credentials not configured"):
        super().__init__(message, code="CREDENTIALS_ERROR", retryable=False)