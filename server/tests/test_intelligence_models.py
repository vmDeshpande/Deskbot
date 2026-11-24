"""Tests for intelligence provider models."""

from __future__ import annotations

import pytest

from app.services.intelligence.models import (
    ConversationTurn,
    ConversationContext,
    FunctionDefinition,
    FunctionExecutionResult,
    IntentResult,
    IntelligenceContext,
    IntelligenceError,
    IntelligenceTimeoutError,
    IntelligenceRateLimitError,
    IntelligenceAPIError,
    IntelligenceInvalidResponseError,
    IntelligenceProviderInfo,
    ResponseGenerationResult,
)


class TestFunctionDefinition:
    def test_creation(self):
        fd = FunctionDefinition(
            name="get_time",
            description="Get current time",
            parameters={"type": "object", "properties": {}},
            required=[],
        )
        assert fd.name == "get_time"
        assert fd.description == "Get current time"

    def test_required_default_empty(self):
        fd = FunctionDefinition(
            name="test",
            description="test",
            parameters={},
        )
        assert fd.required == []


class TestIntentResult:
    def test_creation(self):
        ir = IntentResult(
            intent="get_time",
            arguments={"timezone": "Asia/Kolkata"},
            response_language="hi",
            confidence=0.9,
            reasoning="User asked for time",
        )
        assert ir.intent == "get_time"
        assert ir.arguments == {"timezone": "Asia/Kolkata"}
        assert ir.response_language == "hi"
        assert ir.confidence == 0.9
        assert ir.reasoning == "User asked for time"

    def test_defaults(self):
        ir = IntentResult(
            intent="none",
            arguments={},
            response_language="en",
        )
        assert ir.confidence == 1.0
        assert ir.reasoning is None


class TestResponseGenerationResult:
    def test_creation(self):
        rgr = ResponseGenerationResult(
            response_text="It's 2:30 PM.",
            response_language="en",
            metadata={"model": "llama-3.3-70b-versatile"},
        )
        assert rgr.response_text == "It's 2:30 PM."
        assert rgr.response_language == "en"
        assert rgr.metadata == {"model": "llama-3.3-70b-versatile"}

    def test_metadata_optional(self):
        rgr = ResponseGenerationResult(
            response_text="Hello",
            response_language="en",
        )
        assert rgr.metadata is None


class TestIntelligenceContext:
    def test_creation(self):
        ctx = IntelligenceContext(
            transcript="What time is it?",
            detected_language="en",
            language_confidence=0.95,
            timezone="Asia/Kolkata",
            location="Nashik, India",
            device_id="device-123",
            recording_id="rec-456",
            available_functions=[],
        )
        assert ctx.transcript == "What time is it?"
        assert ctx.detected_language == "en"
        assert ctx.conversation_history == []


class TestFunctionExecutionResult:
    def test_creation(self):
        fer = FunctionExecutionResult(
            intent="get_time",
            arguments={"timezone": "Asia/Kolkata"},
            result={"iso_time": "2024-01-15T14:30:00+05:30"},
            success=True,
        )
        assert fer.intent == "get_time"
        assert fer.success is True
        assert fer.error is None

    def test_with_error(self):
        fer = FunctionExecutionResult(
            intent="get_time",
            arguments={},
            result={},
            success=False,
            error="Function failed",
        )
        assert fer.success is False
        assert fer.error == "Function failed"


class TestIntelligenceProviderInfo:
    def test_creation(self):
        info = IntelligenceProviderInfo(
            name="groq",
            model="llama-3.3-70b-versatile",
            supports_function_calling=True,
            supports_structured_output=True,
        )
        assert info.name == "groq"
        assert info.supports_function_calling is True


class TestConversationTurn:
    def test_creation(self):
        turn = ConversationTurn(
            recording_id="rec-123",
            transcript="What time is it?",
            detected_language="en",
            intent="get_time",
            recognized=True,
            response_text="It's 2:30 PM.",
            response_language="en",
            timestamp=1234567890.0,
        )
        assert turn.recording_id == "rec-123"
        assert turn.intent == "get_time"
        assert turn.recognized is True


class TestConversationContext:
    def test_add_and_get_turn(self):
        ctx = ConversationContext(max_turns=3)
        turn1 = ConversationTurn(
            recording_id="rec-1",
            transcript="Hello",
            detected_language="en",
            intent=None,
            recognized=False,
            response_text="Hi there!",
            response_language="en",
            timestamp=1000.0,
        )
        turn2 = ConversationTurn(
            recording_id="rec-2",
            transcript="What time?",
            detected_language="en",
            intent="get_time",
            recognized=True,
            response_text="2:30 PM",
            response_language="en",
            timestamp=2000.0,
        )
        ctx.add_turn(turn1)
        ctx.add_turn(turn2)
        
        history = ctx.get_history()
        assert len(history) == 2
        assert history[0].recording_id == "rec-1"
        assert history[1].recording_id == "rec-2"

    def test_max_turns_limit(self):
        ctx = ConversationContext(max_turns=2)
        for i in range(5):
            turn = ConversationTurn(
                recording_id=f"rec-{i}",
                transcript=f"Message {i}",
                detected_language="en",
                intent=None,
                recognized=False,
                response_text=f"Response {i}",
                response_language="en",
                timestamp=float(i),
            )
            ctx.add_turn(turn)
        
        history = ctx.get_history()
        assert len(history) == 2
        assert history[0].recording_id == "rec-3"
        assert history[1].recording_id == "rec-4"

    def test_clear(self):
        ctx = ConversationContext(max_turns=3)
        turn = ConversationTurn(
            recording_id="rec-1",
            transcript="Hello",
            detected_language="en",
            intent=None,
            recognized=False,
            response_text="Hi",
            response_language="en",
            timestamp=1000.0,
        )
        ctx.add_turn(turn)
        ctx.clear()
        assert ctx.get_history() == []


class TestIntelligenceErrors:
    def test_base_error(self):
        err = IntelligenceError("Test error", code="TEST_ERROR", retryable=True)
        assert str(err) == "Test error"
        assert err.code == "TEST_ERROR"
        assert err.retryable is True

    def test_timeout_error(self):
        err = IntelligenceTimeoutError()
        assert err.code == "TIMEOUT"
        assert err.retryable is True

    def test_rate_limit_error(self):
        err = IntelligenceRateLimitError()
        assert err.code == "RATE_LIMIT"
        assert err.retryable is True

    def test_api_error_retryable(self):
        err = IntelligenceAPIError("Server error", status_code=500)
        assert err.code == "API_ERROR"
        assert err.retryable is True
        assert err.status_code == 500

    def test_api_error_non_retryable(self):
        err = IntelligenceAPIError("Bad request", status_code=400)
        assert err.retryable is False

    def test_invalid_response_error(self):
        err = IntelligenceInvalidResponseError()
        assert err.code == "INVALID_RESPONSE"
        assert err.retryable is False


class TestResponseGenerationResult:
    def test_with_metadata(self):
        rgr = ResponseGenerationResult(
            response_text="It's 2:30 PM.",
            response_language="en",
            metadata={"model": "llama-3.3-70b-versatile", "tokens": 10},
        )
        assert rgr.metadata["model"] == "llama-3.3-70b-versatile"
        assert rgr.metadata["tokens"] == 10