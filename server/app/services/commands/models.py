"""Command processing models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4


@dataclass(frozen=True)
class CommandIntent:
    """Recognized command intent."""

    name: str
    confidence: float = 1.0


@dataclass(frozen=True)
class CommandContext:
    """Execution context for a command."""

    device_id: str
    recording_id: UUID
    transcript: str
    language: str
    detected_language: str
    language_confidence: float | None
    timezone: str
    location: str


@dataclass(frozen=True)
class CommandResult:
    """Result of command execution."""

    intent: CommandIntent
    recognized: bool
    response_text: str
    response_language: str
    metadata: dict[str, Any] | None = None

    @classmethod
    def unknown(
        cls,
        context: CommandContext,
        transcript: str,
        response_language: str | None = None,
    ) -> CommandResult:
        lang = response_language or context.detected_language or context.language
        fallback_text = {
            "en": "I'm sorry, I can't do that yet. You can ask me about the time, date, or about myself.",
            "hi": "माफ़ कीजिए, मैं अभी यह काम नहीं कर सकता। आप समय, तारीख या मेरे बारे में पूछ सकते हैं।",
            "mr": "माफ करा, मी अजून हे करू शकत नाही. तुम्ही मला वेळ, तारीख किंवा माझ्याबद्दल विचारू शकता.",
        }
        return cls(
            intent=CommandIntent(name="unknown", confidence=0.0),
            recognized=False,
            response_text=fallback_text.get(lang, fallback_text["hi"]),
            response_language=lang,
            metadata={"original_transcript": transcript},
        )


@dataclass(frozen=True)
class CommandEnvelope:
    """Envelope for command.completed message."""

    protocol_version: str = "1.0"
    message_id: UUID = uuid4()
    message_type: str = "command.completed"
    timestamp: str = ""
    payload: dict[str, Any] = None

    @classmethod
    def from_result(cls, result: CommandResult, timestamp: str) -> CommandEnvelope:
        return cls(
            timestamp=timestamp,
            payload={
                "intent": result.intent.name,
                "recognized": result.recognized,
                "detected_language": result.response_language,
                "response_language": result.response_language,
                "response_text": result.response_text,
                "metadata": result.metadata or {},
            },
        )