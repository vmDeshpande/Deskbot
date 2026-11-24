"""Tests for Groq intelligence provider with mocked API responses."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.services.commands.registry import create_registry
from app.services.intelligence.groq_provider import GroqProvider
from app.services.intelligence.models import (
    FunctionExecutionResult,
    IntelligenceContext,
    IntentResult,
    ResponseGenerationResult,
)


class MockGroqResponse:
    """Mock Groq API response."""
    def __init__(self, content=None, tool_calls=None):
        self.choices = [MagicMock()]
        self.choices[0].message = MagicMock()
        self.choices[0].message.content = content
        self.choices[0].message.tool_calls = tool_calls


class MockToolCall:
    """Mock tool call."""
    def __init__(self, function_name, arguments):
        self.function = MagicMock()
        self.function.name = function_name
        self.function.arguments = json.dumps(arguments)


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        groq_api_key="test-api-key",
        groq_model="llama-3.3-70b-versatile",
        intelligence_enabled=True,
        intelligence_provider="groq",
        timezone="Asia/Kolkata",
        default_location="Nashik, India",
        default_language="hi-IN",
    )


@pytest.fixture
def command_registry():
    return create_registry()


@pytest.fixture
def groq_provider(settings, command_registry):
    provider = GroqProvider(settings, command_registry)
    # Mock the client
    provider._client = AsyncMock()
    return provider


@pytest.fixture
def intelligence_context():
    return IntelligenceContext(
        transcript="What time is it?",
        detected_language="en",
        language_confidence=0.95,
        timezone="Asia/Kolkata",
        location="Nashik, India",
        device_id="test-device",
        recording_id="rec-123",
        available_functions=[],
    )


class TestGroqProviderInitialization:
    def test_info_property(self, groq_provider):
        info = groq_provider.info
        assert info.name == "groq"
        assert info.model == "llama-3.3-70b-versatile"
        assert info.supports_function_calling is True
        assert info.supports_structured_output is True


class TestGroqProviderIntentRecognition:
    @pytest.mark.asyncio
    async def test_recognize_intent_time_via_tool_call(self, groq_provider, intelligence_context):
        """Test intent recognition when Groq returns a tool call for get_time."""
        tool_call = MockToolCall("get_time", {"timezone": "Asia/Kolkata"})
        mock_response = MockGroqResponse(content=None, tool_calls=[tool_call])
        groq_provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await groq_provider.recognize_intent(intelligence_context)

        assert result.intent == "get_time"
        assert result.arguments == {"timezone": "Asia/Kolkata"}
        assert result.response_language == "en"
        assert result.confidence == 0.9

    @pytest.mark.asyncio
    async def test_recognize_intent_date_via_tool_call(self, groq_provider, intelligence_context):
        """Test intent recognition for get_date."""
        tool_call = MockToolCall("get_date", {"timezone": "Asia/Kolkata"})
        mock_response = MockGroqResponse(content=None, tool_calls=[tool_call])
        groq_provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await groq_provider.recognize_intent(intelligence_context)

        assert result.intent == "get_date"
        assert result.arguments == {"timezone": "Asia/Kolkata"}

    @pytest.mark.asyncio
    async def test_recognize_intent_identity_via_tool_call(self, groq_provider, intelligence_context):
        """Test intent recognition for assistant_identity."""
        tool_call = MockToolCall("assistant_identity", {})
        mock_response = MockGroqResponse(content=None, tool_calls=[tool_call])
        groq_provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await groq_provider.recognize_intent(intelligence_context)

        assert result.intent == "assistant_identity"
        assert result.arguments == {}

    @pytest.mark.asyncio
    async def test_recognize_intent_help_via_tool_call(self, groq_provider, intelligence_context):
        """Test intent recognition for assistant_help."""
        tool_call = MockToolCall("assistant_help", {})
        mock_response = MockGroqResponse(content=None, tool_calls=[tool_call])
        groq_provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await groq_provider.recognize_intent(intelligence_context)

        assert result.intent == "assistant_help"
        assert result.arguments == {}

    @pytest.mark.asyncio
    async def test_recognize_intent_json_content(self, groq_provider, intelligence_context):
        """Test intent recognition when Groq returns JSON in content."""
        json_content = json.dumps({
            "intent": "get_time",
            "arguments": {"timezone": "Asia/Kolkata"},
            "response_language": "en",
            "confidence": 0.85,
            "reasoning": "User asked for time",
        })
        mock_response = MockGroqResponse(content=json_content, tool_calls=None)
        groq_provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await groq_provider.recognize_intent(intelligence_context)

        assert result.intent == "get_time"
        assert result.arguments == {"timezone": "Asia/Kolkata"}
        assert result.confidence == 0.85

    @pytest.mark.asyncio
    async def test_recognize_intent_none(self, groq_provider, intelligence_context):
        """Test intent recognition for conversational (no function)."""
        json_content = json.dumps({
            "intent": "none",
            "arguments": {},
            "response_language": "en",
            "confidence": 0.5,
            "reasoning": "General conversation",
        })
        mock_response = MockGroqResponse(content=json_content, tool_calls=None)
        groq_provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await groq_provider.recognize_intent(intelligence_context)

        assert result.intent == "none"
        assert result.arguments == {}

    @pytest.mark.asyncio
    async def test_recognize_intent_invalid_function_rejected(self, groq_provider, intelligence_context):
        """Test that invalid function calls are rejected."""
        tool_call = MockToolCall("invalid_function", {})
        mock_response = MockGroqResponse(content=None, tool_calls=[tool_call])
        groq_provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await groq_provider.recognize_intent(intelligence_context)

        assert result.intent == "none"
        assert result.confidence == 0.0
        assert "Invalid function" in result.reasoning

    @pytest.mark.asyncio
    async def test_recognize_intent_malformed_json_fallback(self, groq_provider, intelligence_context):
        """Test fallback when Groq returns malformed JSON."""
        mock_response = MockGroqResponse(content="not valid json", tool_calls=None)
        groq_provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await groq_provider.recognize_intent(intelligence_context)

        assert result.intent == "none"
        assert result.confidence == 0.3


class TestGroqProviderResponseGeneration:
    @pytest.mark.asyncio
    async def test_generate_response_time(self, groq_provider, intelligence_context):
        """Test response generation for time query."""
        execution_result = FunctionExecutionResult(
            intent="get_time",
            arguments={"timezone": "Asia/Kolkata"},
            result={"iso_time": "2024-01-15T14:30:00+05:30", "timezone": "Asia/Kolkata"},
            success=True,
        )

        json_response = json.dumps({
            "response_text": "It's 2:30 PM.",
            "response_language": "en",
        })
        mock_response = MockGroqResponse(content=json_response)
        groq_provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await groq_provider.generate_response(intelligence_context, execution_result)

        assert result.response_text == "It's 2:30 PM."
        assert result.response_language == "en"

    @pytest.mark.asyncio
    async def test_generate_response_hindi(self, groq_provider, intelligence_context):
        """Test response generation in Hindi."""
        context = IntelligenceContext(
            transcript="What time is it?",
            detected_language="hi",
            language_confidence=0.95,
            timezone="Asia/Kolkata",
            location="Nashik, India",
            device_id="test-device",
            recording_id="rec-123",
            available_functions=[],
        )
        execution_result = FunctionExecutionResult(
            intent="get_time",
            arguments={"timezone": "Asia/Kolkata"},
            result={"iso_time": "2024-01-15T14:30:00+05:30"},
            success=True,
        )

        json_response = json.dumps({
            "response_text": "अभी दोपहर के 2:30 बजे हैं।",
            "response_language": "hi",
        })
        mock_response = MockGroqResponse(content=json_response)
        groq_provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await groq_provider.generate_response(context, execution_result)

        assert result.response_text == "अभी दोपहर के 2:30 बजे हैं।"
        assert result.response_language == "hi"

    @pytest.mark.asyncio
    async def test_generate_response_marathi(self, groq_provider, intelligence_context):
        """Test response generation in Marathi."""
        context = IntelligenceContext(
            transcript="What time is it?",
            detected_language="mr",
            language_confidence=0.95,
            timezone="Asia/Kolkata",
            location="Nashik, India",
            device_id="test-device",
            recording_id="rec-123",
            available_functions=[],
        )
        execution_result = FunctionExecutionResult(
            intent="get_date",
            arguments={"timezone": "Asia/Kolkata"},
            result={"iso_date": "2024-01-15", "timezone": "Asia/Kolkata"},
            success=True,
        )

        json_response = json.dumps({
            "response_text": "आज सोमवार, 15 जनवरी 2024 आहे.",
            "response_language": "mr",
        })
        mock_response = MockGroqResponse(content=json_response)
        groq_provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await groq_provider.generate_response(context, execution_result)

        assert result.response_text == "आज सोमवार, 15 जनवरी 2024 आहे."
        assert result.response_language == "mr"

    @pytest.mark.asyncio
    async def test_generate_response_malformed_json_fallback(self, groq_provider, intelligence_context):
        """Test fallback when response JSON is malformed."""
        execution_result = FunctionExecutionResult(
            intent="get_time",
            arguments={},
            result={"iso_time": "2024-01-15T14:30:00+05:30"},
            success=True,
        )

        mock_response = MockGroqResponse(content="not valid json")
        groq_provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await groq_provider.generate_response(intelligence_context, execution_result)

        # Should fall back to simple response
        assert "2:30" in result.response_text or "14:30" in result.response_text
        assert result.metadata.get("fallback") is True


class TestGroqProviderConversationalResponse:
    @pytest.mark.asyncio
    async def test_generate_conversational_response(self, groq_provider, intelligence_context):
        """Test conversational response generation."""
        json_response = json.dumps({
            "response_text": "I'm not sure how to help with that. You can ask me about the time or date.",
            "response_language": "en",
        })
        mock_response = MockGroqResponse(content=json_response)
        groq_provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await groq_provider.generate_conversational_response(intelligence_context)

        assert "not sure" in result.response_text.lower()
        assert result.response_language == "en"

    @pytest.mark.asyncio
    async def test_generate_conversational_response_hindi(self, groq_provider, intelligence_context):
        """Test conversational response in Hindi."""
        context = IntelligenceContext(
            transcript="कैसे हो?",
            detected_language="hi",
            language_confidence=0.95,
            timezone="Asia/Kolkata",
            location="Nashik, India",
            device_id="test-device",
            recording_id="rec-123",
            available_functions=[],
        )

        json_response = json.dumps({
            "response_text": "मैं ठीक हूँ! आप समय या तारीख पूछ सकते हैं।",
            "response_language": "hi",
        })
        mock_response = MockGroqResponse(content=json_response)
        groq_provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await groq_provider.generate_conversational_response(context)

        assert "ठीक" in result.response_text
        assert result.response_language == "hi"


class TestGroqProviderErrorHandling:
    @pytest.mark.asyncio
    async def test_recognize_intent_api_error(self, groq_provider, intelligence_context):
        """Test handling of API errors."""
        from app.services.intelligence.models import IntelligenceAPIError
        groq_provider._client.chat.completions.create = AsyncMock(
            side_effect=IntelligenceAPIError("API Error", status_code=500)
        )

        with pytest.raises(IntelligenceAPIError):
            await groq_provider.recognize_intent(intelligence_context)

    @pytest.mark.asyncio
    async def test_recognize_intent_rate_limit(self, groq_provider, intelligence_context):
        """Test handling of rate limit errors."""
        from app.services.intelligence.models import IntelligenceRateLimitError
        groq_provider._client.chat.completions.create = AsyncMock(
            side_effect=IntelligenceRateLimitError("Rate limited")
        )

        with pytest.raises(IntelligenceRateLimitError):
            await groq_provider.recognize_intent(intelligence_context)

    @pytest.mark.asyncio
    async def test_recognize_intent_timeout(self, groq_provider, intelligence_context):
        """Test handling of timeout errors."""
        from app.services.intelligence.models import IntelligenceTimeoutError
        groq_provider._client.chat.completions.create = AsyncMock(
            side_effect=IntelligenceTimeoutError("Timeout")
        )

        with pytest.raises(IntelligenceTimeoutError):
            await groq_provider.recognize_intent(intelligence_context)


class TestGroqProviderLanguageSelection:
    @pytest.mark.asyncio
    async def test_english_language_selection(self, groq_provider):
        """Test that English input results in English response."""
        context = IntelligenceContext(
            transcript="What time is it?",
            detected_language="en",
            language_confidence=0.95,
            timezone="Asia/Kolkata",
            location="Nashik, India",
            device_id="test-device",
            recording_id="rec-123",
            available_functions=[],
        )
        
        tool_call = MockToolCall("get_time", {"timezone": "Asia/Kolkata"})
        mock_response = MockGroqResponse(content=None, tool_calls=[tool_call])
        groq_provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await groq_provider.recognize_intent(context)
        assert result.response_language == "en"

    @pytest.mark.asyncio
    async def test_hindi_language_selection(self, groq_provider):
        """Test that Hindi input results in Hindi response."""
        context = IntelligenceContext(
            transcript="अभी कितने बजे हैं?",
            detected_language="hi",
            language_confidence=0.95,
            timezone="Asia/Kolkata",
            location="Nashik, India",
            device_id="test-device",
            recording_id="rec-123",
            available_functions=[],
        )
        
        tool_call = MockToolCall("get_time", {"timezone": "Asia/Kolkata"})
        mock_response = MockGroqResponse(content=None, tool_calls=[tool_call])
        groq_provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await groq_provider.recognize_intent(context)
        assert result.response_language == "hi"

    @pytest.mark.asyncio
    async def test_marathi_language_selection(self, groq_provider):
        """Test that Marathi input results in Marathi response."""
        context = IntelligenceContext(
            transcript="आता किती वाजले?",
            detected_language="mr",
            language_confidence=0.95,
            timezone="Asia/Kolkata",
            location="Nashik, India",
            device_id="test-device",
            recording_id="rec-123",
            available_functions=[],
        )
        
        tool_call = MockToolCall("get_time", {"timezone": "Asia/Kolkata"})
        mock_response = MockGroqResponse(content=None, tool_calls=[tool_call])
        groq_provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await groq_provider.recognize_intent(context)
        assert result.response_language == "mr"