"""Intelligence provider models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FunctionDefinition:
    """Definition of a callable function for the intelligence provider."""

    name: str
    description: str
    parameters: dict[str, Any]
    required: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IntentResult:
    """Structured intent result from intelligence provider."""

    intent: str
    arguments: dict[str, Any]
    response_language: str
    confidence: float = 1.0
    reasoning: str | None = None


@dataclass(frozen=True)
class ResponseGenerationResult:
    """Natural language response from intelligence provider."""

    response_text: str
    response_language: str
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class IntelligenceContext:
    """Context passed to intelligence provider for a request."""

    transcript: str
    detected_language: str
    language_confidence: float | None
    timezone: str
    location: str
    device_id: str
    recording_id: str
    available_functions: list[FunctionDefinition]
    conversation_history: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class FunctionExecutionResult:
    """Result of a function execution to be passed back for response generation."""

    intent: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    success: bool
    error: str | None = None


@dataclass(frozen=True)
class IntelligenceProviderInfo:
    """Information about an intelligence provider."""

    name: str
    model: str
    supports_function_calling: bool
    supports_structured_output: bool


@dataclass(frozen=True)
class ConversationTurn:
    """A single turn in the conversation history."""

    recording_id: str
    transcript: str
    detected_language: str
    intent: str | None
    recognized: bool
    response_text: str
    response_language: str
    timestamp: float


@dataclass
class ConversationContext:
    """Manages conversation context for the intelligence provider."""
    
    max_turns: int = 5
    _history: list = None
    
    def __post_init__(self):
        if self._history is None:
            self._history = []

    def add_turn(self, turn: ConversationTurn) -> None:
        self._history.append(turn)
        if len(self._history) > self.max_turns:
            self._history = self._history[-self.max_turns:]

    def get_history(self, max_turns: int | None = None) -> list[ConversationTurn]:
        turns = self._history
        if max_turns is not None:
            turns = turns[-max_turns:]
        return turns

    def clear(self) -> None:
        self._history.clear()


class IntelligenceError(Exception):
    """Base exception for intelligence provider errors."""

    def __init__(self, message: str, code: str = "INTELLIGENCE_ERROR", retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class IntelligenceTimeoutError(IntelligenceError):
    """Raised when intelligence provider request times out."""

    def __init__(self, message: str = "Intelligence provider request timed out"):
        super().__init__(message, code="TIMEOUT", retryable=True)


class IntelligenceRateLimitError(IntelligenceError):
    """Raised when intelligence provider rate limit is exceeded."""

    def __init__(self, message: str = "Intelligence provider rate limit exceeded"):
        super().__init__(message, code="RATE_LIMIT", retryable=True)


class IntelligenceAPIError(IntelligenceError):
    """Raised when intelligence provider returns an API error."""

    def __init__(self, message: str, status_code: int | None = None):
        retryable = status_code in (429, 500, 502, 503, 504) if status_code else False
        super().__init__(message, code="API_ERROR", retryable=retryable)
        self.status_code = status_code


class IntelligenceInvalidResponseError(IntelligenceError):
    """Raised when intelligence provider returns invalid/malformed response."""

    def __init__(self, message: str = "Invalid response from intelligence provider"):
        super().__init__(message, code="INVALID_RESPONSE", retryable=False)