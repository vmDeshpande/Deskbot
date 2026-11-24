"""Speech recognition models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TranscriptionResult:
    """Result of speech transcription."""

    text: str
    language: str
    language_probability: float | None = None
    is_final: bool = True
    metadata: dict[str, Any] | None = None

    @property
    def is_empty(self) -> bool:
        return not self.text or not self.text.strip()


@dataclass(frozen=True)
class PartialTranscriptionResult:
    """Partial (interim) transcription result."""

    text: str
    is_final: bool = False


@dataclass(frozen=True)
class STTProviderInfo:
    """Information about an STT provider."""

    name: str
    model: str
    supported_languages: list[str]
    streaming: bool