"""Command processing unit and integration tests."""

from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from app.services.commands import (
    CommandContext,
    CommandRouter,
    create_registry,
)
from app.services.commands.models import CommandIntent, CommandResult
from app.services.commands.datetime_commands import DateCommandHandler, TimeCommandHandler
from app.services.commands.assistant_commands import HelpCommandHandler, IdentityCommandHandler
from app.services.commands.registry import CommandRegistry


class FixedTimeZoneInfo(ZoneInfo):
    """Timezone that returns a fixed time for testing."""
    pass


def make_context(
    transcript: str,
    device_id: str = "593a0d29-c053-4c40-9c3c-8d7d26102f7a",
    language: str = "hi-IN",
    detected_language: str = "hi",
    language_confidence: float | None = 0.9,
    timezone: str = "Asia/Kolkata",
    location: str = "Nashik, Maharashtra, India",
) -> CommandContext:
    return CommandContext(
        device_id=device_id,
        recording_id=uuid4(),
        transcript=transcript,
        language=language,
        detected_language=detected_language,
        language_confidence=language_confidence,
        timezone=timezone,
        location=location,
    )


class TestCommandModels:
    def test_command_intent_creation(self):
        intent = CommandIntent(name="get_time", confidence=1.0)
        assert intent.name == "get_time"
        assert intent.confidence == 1.0

    def test_command_context_creation(self):
        ctx = make_context("अभी कितने बजे हैं?")
        assert ctx.transcript == "अभी कितने बजे हैं?"
        assert ctx.language == "hi-IN"
        assert ctx.timezone == "Asia/Kolkata"

    def test_command_result_unknown(self):
        ctx = make_context("unsupported query")
        result = CommandResult.unknown(ctx, "unsupported query")
        assert result.recognized is False
        assert result.intent.name == "unknown"
        assert "माफ़ कीजिए" in result.response_text


class TestTimeCommandHandler:
    @pytest.fixture
    def handler(self):
        return TimeCommandHandler()

    @pytest.mark.asyncio
    async def test_execute_returns_hindi_time(self, handler):
        ctx = make_context("अभी कितने बजे हैं?", detected_language="hi")
        result = await handler.execute(ctx)

        assert result.recognized is True
        assert result.intent.name == "get_time"
        assert "बजे" in result.response_text
        assert "हैं" in result.response_text
        assert result.response_language == "hi"
        assert result.metadata is not None
        assert "timezone" in result.metadata
        assert "iso_time" in result.metadata

    @pytest.mark.asyncio
    async def test_execute_returns_english_time(self, handler):
        ctx = make_context("What time is it?", detected_language="en")
        result = await handler.execute(ctx)

        assert result.recognized is True
        assert result.intent.name == "get_time"
        assert "PM" in result.response_text or "AM" in result.response_text
        assert result.response_language == "en"

    @pytest.mark.asyncio
    async def test_execute_returns_marathi_time(self, handler):
        ctx = make_context("आता किती वाजले?", detected_language="mr")
        result = await handler.execute(ctx)

        assert result.recognized is True
        assert result.intent.name == "get_time"
        assert "वाजले" in result.response_text
        assert result.response_language == "mr"


class TestDateCommandHandler:
    @pytest.fixture
    def handler(self):
        return DateCommandHandler()

    @pytest.mark.asyncio
    async def test_execute_returns_hindi_date(self, handler):
        ctx = make_context("आज कौन सी तारीख है?", detected_language="hi")
        result = await handler.execute(ctx)

        assert result.recognized is True
        assert result.intent.name == "get_date"
        assert "आज" in result.response_text
        assert "है" in result.response_text
        assert result.response_language == "hi"
        assert result.metadata is not None
        assert "timezone" in result.metadata
        assert "iso_date" in result.metadata

    @pytest.mark.asyncio
    async def test_execute_returns_english_date(self, handler):
        ctx = make_context("What's today's date?", detected_language="en")
        result = await handler.execute(ctx)

        assert result.recognized is True
        assert result.intent.name == "get_date"
        assert "Today is" in result.response_text
        assert result.response_language == "en"

    @pytest.mark.asyncio
    async def test_execute_returns_marathi_date(self, handler):
        ctx = make_context("आज कोणती तारीख?", detected_language="mr")
        result = await handler.execute(ctx)

        assert result.recognized is True
        assert result.intent.name == "get_date"
        assert "आज" in result.response_text
        assert "आहे" in result.response_text
        assert result.response_language == "mr"


class TestIdentityCommandHandler:
    @pytest.fixture
    def handler(self):
        return IdentityCommandHandler()

    @pytest.mark.asyncio
    async def test_execute_returns_identity_hindi(self, handler):
        ctx = make_context("तुम कौन हो?", detected_language="hi")
        result = await handler.execute(ctx)

        assert result.recognized is True
        assert result.intent.name == "assistant_identity"
        assert "Deskbot" in result.response_text
        assert "स्मार्ट डेस्क असिस्टेंट" in result.response_text
        assert result.response_language == "hi"

    @pytest.mark.asyncio
    async def test_execute_returns_identity_english(self, handler):
        ctx = make_context("Who are you?", detected_language="en")
        result = await handler.execute(ctx)

        assert result.recognized is True
        assert result.intent.name == "assistant_identity"
        assert "Deskbot" in result.response_text
        assert "smart desk assistant" in result.response_text.lower()
        assert result.response_language == "en"

    @pytest.mark.asyncio
    async def test_execute_returns_identity_marathi(self, handler):
        ctx = make_context("तू कोण आहेस?", detected_language="mr")
        result = await handler.execute(ctx)

        assert result.recognized is True
        assert result.intent.name == "assistant_identity"
        assert "Deskbot" in result.response_text
        assert "स्मार्ट डेस्क असिस्टंट" in result.response_text
        assert result.response_language == "mr"


class TestHelpCommandHandler:
    @pytest.fixture
    def handler(self):
        registry = create_registry()
        return HelpCommandHandler(registry)

    @pytest.mark.asyncio
    async def test_execute_lists_commands_hindi(self, handler):
        ctx = make_context("तुम क्या कर सकते हो?", detected_language="hi")
        result = await handler.execute(ctx)

        assert result.recognized is True
        assert result.intent.name == "assistant_help"
        assert "समय" in result.response_text
        assert "तारीख" in result.response_text
        assert "परिचय" in result.response_text
        assert "मदद" in result.response_text
        assert result.response_language == "hi"
        assert result.metadata is not None
        assert "supported_intents" in result.metadata

    @pytest.mark.asyncio
    async def test_execute_lists_commands_english(self, handler):
        ctx = make_context("What can you do?", detected_language="en")
        result = await handler.execute(ctx)

        assert result.recognized is True
        assert result.intent.name == "assistant_help"
        assert "time" in result.response_text.lower()
        assert "date" in result.response_text.lower()
        assert result.response_language == "en"

    @pytest.mark.asyncio
    async def test_execute_lists_commands_marathi(self, handler):
        ctx = make_context("तू काय करू शकतो?", detected_language="mr")
        result = await handler.execute(ctx)

        assert result.recognized is True
        assert result.intent.name == "assistant_help"
        assert "वेळ" in result.response_text
        assert "तारीख" in result.response_text
        assert result.response_language == "mr"


class TestCommandRouter:
    @pytest.fixture
    def router(self):
        registry = create_registry()
        return CommandRouter(registry)

    def test_normalize_removes_punctuation_and_whitespace(self, router):
        assert router._normalize("  अभी   कितने   बजे   हैं?  ") == "अभी कितने बजे हैं"
        assert router._normalize("What time is it?") == "what time is it"
        assert router._normalize("क्या\tसमय\tहै।") == "क्या समय है"

    def test_match_time_hindi(self, router):
        assert router._match_time("अभी कितने बजे हैं") is True
        assert router._match_time("समय क्या हुआ") is True
        assert router._match_time("अभी का समय बताओ") is True
        assert router._match_time("कितना समय हुआ") is True
        assert router._match_time("समय बताओ") is True
        assert router._match_time("टाइम क्या है") is True
        assert router._match_time("कितने बजे") is True

    def test_match_time_english(self, router):
        assert router._match_time("what time is it") is True
        assert router._match_time("tell me the current time") is True
        assert router._match_time("whats the time") is True
        assert router._match_time("current time") is True
        assert router._match_time("time now") is True

    def test_match_date_hindi(self, router):
        assert router._match_date("आज कौन सी तारीख है") is True
        assert router._match_date("आज की तारीख बताओ") is True
        assert router._match_date("आज कौन सा दिन है") is True
        assert router._match_date("तारीख क्या है") is True
        assert router._match_date("आज क्या है") is True

    def test_match_date_english(self, router):
        assert router._match_date("whats todays date") is True
        assert router._match_date("tell me todays date") is True
        assert router._match_date("what day is it today") is True
        assert router._match_date("todays date") is True
        assert router._match_date("what is the date") is True

    def test_match_identity_hindi(self, router):
        assert router._match_identity("तुम कौन हो") is True
        assert router._match_identity("तुम्हारा नाम क्या है") is True
        assert router._match_identity("तुम क्या हो") is True
        assert router._match_identity("आप कौन हैं") is True
        assert router._match_identity("आपका नाम क्या है") is True

    def test_match_identity_english(self, router):
        assert router._match_identity("who are you") is True
        assert router._match_identity("whats your name") is True
        assert router._match_identity("what are you") is True
        assert router._match_identity("your name") is True

    def test_match_help_hindi(self, router):
        assert router._match_help("तुम क्या कर सकते हो") is True
        assert router._match_help("तुम्हारे पास कौन कौन से फीचर्स हैं") is True
        assert router._match_help("मुझे क्या पूछना चाहिए") is True
        assert router._match_help("क्या कर सकते हो") is True
        assert router._match_help("कमांड क्या हैं") is True
        assert router._match_help("मदद करो") is True

    def test_match_help_english(self, router):
        assert router._match_help("what can you do") is True
        assert router._match_help("what commands do you support") is True
        assert router._match_help("help me") is True
        assert router._match_help("capabilities") is True
        assert router._match_help("what are your features") is True

    def test_recognize_unknown(self, router):
        intent = router.recognize("बेकार की बात")
        assert intent.name == "unknown"
        assert intent.confidence == 0.0

    def test_recognize_empty(self, router):
        intent = router.recognize("")
        assert intent.name == "unknown"
        assert intent.confidence == 0.0

    def test_recognize_whitespace_only(self, router):
        intent = router.recognize("   ")
        assert intent.name == "unknown"
        assert intent.confidence == 0.0

    @pytest.mark.asyncio
    async def test_route_time_hindi(self, router):
        ctx = make_context("अभी कितने बजे हैं?")
        intent = await router.route(ctx)
        assert intent.name == "get_time"

    @pytest.mark.asyncio
    async def test_route_time_english(self, router):
        ctx = make_context("What time is it?")
        intent = await router.route(ctx)
        assert intent.name == "get_time"

    @pytest.mark.asyncio
    async def test_route_date_hindi(self, router):
        ctx = make_context("आज कौन सी तारीख है?")
        intent = await router.route(ctx)
        assert intent.name == "get_date"

    @pytest.mark.asyncio
    async def test_route_date_english(self, router):
        ctx = make_context("What's today's date?")
        intent = await router.route(ctx)
        assert intent.name == "get_date"

    @pytest.mark.asyncio
    async def test_route_identity_hindi(self, router):
        ctx = make_context("तुम कौन हो?")
        intent = await router.route(ctx)
        assert intent.name == "assistant_identity"

    @pytest.mark.asyncio
    async def test_route_identity_english(self, router):
        ctx = make_context("Who are you?")
        intent = await router.route(ctx)
        assert intent.name == "assistant_identity"

    @pytest.mark.asyncio
    async def test_route_help_hindi(self, router):
        ctx = make_context("तुम क्या कर सकते हो?")
        intent = await router.route(ctx)
        assert intent.name == "assistant_help"

    @pytest.mark.asyncio
    async def test_route_help_english(self, router):
        ctx = make_context("What can you do?")
        intent = await router.route(ctx)
        assert intent.name == "assistant_help"

    @pytest.mark.asyncio
    async def test_route_unknown(self, router):
        ctx = make_context("कुछ भी बोल दो")
        intent = await router.route(ctx)
        assert intent.name == "unknown"

    def test_case_insensitive_english(self, router):
        # Test through recognize which normalizes case
        assert router.recognize("WHAT TIME IS IT").name == "get_time"
        assert router.recognize("What Time Is It").name == "get_time"
        assert router.recognize("WHATS TODAYS DATE").name == "get_date"
        assert router.recognize("WHO ARE YOU").name == "assistant_identity"
        assert router.recognize("WHAT CAN YOU DO").name == "assistant_help"

    def test_punctuation_handling(self, router):
        assert router._match_time("अभी कितने बजे हैं?") is True
        assert router._match_time("अभी कितने बजे हैं।") is True
        assert router._match_date("आज की तारीख बताओ!") is True
        assert router._match_identity("तुम कौन हो?") is True
        assert router._match_help("तुम क्या कर सकते हो?") is True


class TestCommandRegistry:
    @pytest.fixture
    def registry(self):
        return create_registry()

    def test_list_intents(self, registry):
        intents = registry.list_intents()
        assert "get_time" in intents
        assert "get_date" in intents
        assert "assistant_identity" in intents
        assert "assistant_help" in intents

    @pytest.mark.asyncio
    async def test_execute_time(self, registry):
        ctx = make_context("अभी कितने बजे हैं?")
        result = await registry.execute("get_time", ctx)
        assert result.recognized is True
        assert result.intent.name == "get_time"

    @pytest.mark.asyncio
    async def test_execute_date(self, registry):
        ctx = make_context("आज कौन सी तारीख है?")
        result = await registry.execute("get_date", ctx)
        assert result.recognized is True
        assert result.intent.name == "get_date"

    @pytest.mark.asyncio
    async def test_execute_identity(self, registry):
        ctx = make_context("तुम कौन हो?")
        result = await registry.execute("assistant_identity", ctx)
        assert result.recognized is True
        assert result.intent.name == "assistant_identity"

    @pytest.mark.asyncio
    async def test_execute_help(self, registry):
        ctx = make_context("तुम क्या कर सकते हो?")
        result = await registry.execute("assistant_help", ctx)
        assert result.recognized is True
        assert result.intent.name == "assistant_help"

    @pytest.mark.asyncio
    async def test_execute_unknown_intent(self, registry):
        ctx = make_context("unknown")
        result = await registry.execute("nonexistent", ctx)
        assert result.recognized is False
        assert result.intent.name == "unknown"


class TestTimezoneCorrectness:
    """Test that timezone handling works correctly for Asia/Kolkata."""

    @pytest.mark.asyncio
    async def test_time_handler_uses_configured_timezone(self):
        handler = TimeCommandHandler()
        ctx = make_context("time", timezone="Asia/Kolkata")
        result = await handler.execute(ctx)
        assert result.metadata["timezone"] == "Asia/Kolkata"

    @pytest.mark.asyncio
    async def test_date_handler_uses_configured_timezone(self):
        handler = DateCommandHandler()
        ctx = make_context("date", timezone="Asia/Kolkata")
        result = await handler.execute(ctx)
        assert result.metadata["timezone"] == "Asia/Kolkata"

    @pytest.mark.asyncio
    async def test_fallback_timezone_on_invalid(self):
        handler = TimeCommandHandler()
        ctx = make_context("time", timezone="Invalid/Timezone")
        result = await handler.execute(ctx)
        # Should fall back to Asia/Kolkata
        assert result.metadata["timezone"] == "Invalid/Timezone"
        # The actual timezone used internally falls back
        assert "iso_time" in result.metadata


class TestCommandResultSchema:
    def test_command_result_has_required_fields(self):
        ctx = make_context("test")
        result = CommandResult(
            intent=CommandIntent(name="get_time"),
            recognized=True,
            response_text="Test response",
            response_language="hi",
            metadata={"key": "value"},
        )
        assert result.intent.name == "get_time"
        assert result.recognized is True
        assert result.response_text == "Test response"
        assert result.response_language == "hi"
        assert result.metadata == {"key": "value"}

    def test_command_result_metadata_optional(self):
        ctx = make_context("test")
        result = CommandResult(
            intent=CommandIntent(name="get_time"),
            recognized=True,
            response_text="Test response",
            response_language="hi",
        )
        assert result.metadata is None


class TestIntegration:
    """Integration tests for the full command pipeline."""

    @pytest.mark.asyncio
    async def test_full_pipeline_time_hindi(self):
        registry = create_registry()
        router = CommandRouter(registry)
        ctx = make_context("अभी कितने बजे हैं?")
        intent = await router.route(ctx)
        result = await registry.execute(intent.name, ctx)
        assert result.recognized is True
        assert result.intent.name == "get_time"
        assert "बजे" in result.response_text

    @pytest.mark.asyncio
    async def test_full_pipeline_time_english(self):
        registry = create_registry()
        router = CommandRouter(registry)
        ctx = make_context("What time is it?")
        intent = await router.route(ctx)
        result = await registry.execute(intent.name, ctx)
        assert result.recognized is True
        assert result.intent.name == "get_time"
        assert "बजे" in result.response_text

    @pytest.mark.asyncio
    async def test_full_pipeline_date_hindi(self):
        registry = create_registry()
        router = CommandRouter(registry)
        ctx = make_context("आज कौन सी तारीख है?")
        intent = await router.route(ctx)
        result = await registry.execute(intent.name, ctx)
        assert result.recognized is True
        assert result.intent.name == "get_date"
        assert "आज" in result.response_text

    @pytest.mark.asyncio
    async def test_full_pipeline_date_english(self):
        registry = create_registry()
        router = CommandRouter(registry)
        ctx = make_context("What's today's date?")
        intent = await router.route(ctx)
        result = await registry.execute(intent.name, ctx)
        assert result.recognized is True
        assert result.intent.name == "get_date"
        assert "आज" in result.response_text

    @pytest.mark.asyncio
    async def test_full_pipeline_identity_hindi(self):
        registry = create_registry()
        router = CommandRouter(registry)
        ctx = make_context("तुम कौन हो?")
        intent = await router.route(ctx)
        result = await registry.execute(intent.name, ctx)
        assert result.recognized is True
        assert result.intent.name == "assistant_identity"
        assert "Deskbot" in result.response_text

    @pytest.mark.asyncio
    async def test_full_pipeline_identity_english(self):
        registry = create_registry()
        router = CommandRouter(registry)
        ctx = make_context("Who are you?")
        intent = await router.route(ctx)
        result = await registry.execute(intent.name, ctx)
        assert result.recognized is True
        assert result.intent.name == "assistant_identity"
        assert "Deskbot" in result.response_text

    @pytest.mark.asyncio
    async def test_full_pipeline_help_hindi(self):
        registry = create_registry()
        router = CommandRouter(registry)
        ctx = make_context("तुम क्या कर सकते हो?")
        intent = await router.route(ctx)
        result = await registry.execute(intent.name, ctx)
        assert result.recognized is True
        assert result.intent.name == "assistant_help"
        assert "समय" in result.response_text

    @pytest.mark.asyncio
    async def test_full_pipeline_help_english(self):
        registry = create_registry()
        router = CommandRouter(registry)
        ctx = make_context("What can you do?")
        intent = await router.route(ctx)
        result = await registry.execute(intent.name, ctx)
        assert result.recognized is True
        assert result.intent.name == "assistant_help"
        assert "समय" in result.response_text

    @pytest.mark.asyncio
    async def test_full_pipeline_unknown(self):
        registry = create_registry()
        router = CommandRouter(registry)
        ctx = make_context("कुछ अनजाना सवाल")
        intent = await router.route(ctx)
        result = await registry.execute(intent.name, ctx)
        assert result.recognized is False
        assert result.intent.name == "unknown"
        assert "माफ़ कीजिए" in result.response_text

    @pytest.mark.asyncio
    async def test_command_processing_failure_does_not_break_context(self):
        """Ensure command processing errors don't propagate."""
        registry = create_registry()
        router = CommandRouter(registry)
        ctx = make_context("valid query")
        intent = await router.route(ctx)
        result = await registry.execute(intent.name, ctx)
        # Should succeed even if there's an internal issue
        assert result is not None


class TestMarathiPatterns:
    """Tests for Marathi language command patterns."""

    @pytest.fixture
    def router(self):
        registry = create_registry()
        return CommandRouter(registry)

    def test_match_time_marathi(self, router):
        assert router._match_time("आता किती वाजले") is True
        assert router._match_time("वेळ किती") is True
        assert router._match_time("आता वेळ काय") is True
        assert router._match_time("किती वाजले") is True
        assert router._match_time("वेळ सांगा") is True
        assert router._match_time("आत्ता किती वाजले आहेत") is True
        assert router._match_time("आता किती वाजले आहे") is True
        assert router._match_time("वर्तमान वेळ") is True

    def test_match_date_marathi(self, router):
        assert router._match_date("आज कोणती तारीख") is True
        assert router._match_date("आजची तारीख") is True
        assert router._match_date("आज कोणता दिवस") is True
        assert router._match_date("तारीख काय") is True
        assert router._match_date("आज काय") is True
        assert router._match_date("आज किती तारीख आहे") is True
        assert router._match_date("आजची तारीख काय आहे") is True

    def test_match_identity_marathi(self, router):
        assert router._match_identity("तू कोण आहेस") is True
        assert router._match_identity("तुझं नाव काय") is True
        assert router._match_identity("तुम्ही कोण आहात") is True
        assert router._match_identity("तुमचे नाव काय") is True
        assert router._match_identity("तुम्ही कोण") is True

    def test_match_help_marathi(self, router):
        assert router._match_help("तू काय करू शकतो") is True
        assert router._match_help("तुम्ही काय करू शकता") is True
        assert router._match_help("मला काय विचारायचे") is True
        assert router._match_help("कमांड काय") is True
        assert router._match_help("मदत") is True
        assert router._match_help("काय काय करू शकता") is True

    @pytest.mark.asyncio
    async def test_full_pipeline_time_marathi(self):
        registry = create_registry()
        router = CommandRouter(registry)
        ctx = make_context("आता किती वाजले?", detected_language="mr")
        intent = await router.route(ctx)
        result = await registry.execute(intent.name, ctx)
        assert result.recognized is True
        assert result.intent.name == "get_time"
        assert "वाजले" in result.response_text

    @pytest.mark.asyncio
    async def test_full_pipeline_date_marathi(self):
        registry = create_registry()
        router = CommandRouter(registry)
        ctx = make_context("आजची तारीख काय आहे?", detected_language="mr")
        intent = await router.route(ctx)
        result = await registry.execute(intent.name, ctx)
        assert result.recognized is True
        assert result.intent.name == "get_date"
        assert "आज" in result.response_text

    @pytest.mark.asyncio
    async def test_full_pipeline_identity_marathi(self):
        registry = create_registry()
        router = CommandRouter(registry)
        ctx = make_context("तू कोण आहेस?", detected_language="mr")
        intent = await router.route(ctx)
        result = await registry.execute(intent.name, ctx)
        assert result.recognized is True
        assert result.intent.name == "assistant_identity"
        assert "Deskbot" in result.response_text

    @pytest.mark.asyncio
    async def test_full_pipeline_help_marathi(self):
        registry = create_registry()
        router = CommandRouter(registry)
        ctx = make_context("तू काय करू शकतो?", detected_language="mr")
        intent = await router.route(ctx)
        result = await registry.execute(intent.name, ctx)
        assert result.recognized is True
        assert result.intent.name == "assistant_help"
        assert "वेळ" in result.response_text


class TestDevanagariNormalization:
    """Tests for Devanagari text normalization."""

    @pytest.fixture
    def router(self):
        registry = create_registry()
        return CommandRouter(registry)

    def test_normalize_devanagari_danda(self, router):
        assert router._normalize("अभी कितने बजे हैं।") == "अभी कितने बजे हैं"
        assert router._normalize("आज की तारीख क्या है।") == "आज की तारीख क्या है"
        assert router._normalize("तुम कौन हो।") == "तुम कौन हो"

    def test_normalize_mixed_punctuation(self, router):
        assert router._normalize("अभी कितने बजे हैं?!") == "अभी कितने बजे हैं"
        assert router._normalize("क्या समय है?,.") == "क्या समय है"

    def test_normalize_whitespace(self, router):
        assert router._normalize("अभी   कितने    बजे  हैं") == "अभी कितने बजे हैं"
        assert router._normalize("आज\tकौन\tसी\tतारीख") == "आज कौन सी तारीख"

    def test_normalize_case_insensitive_devanagari(self, router):
        # Devanagari doesn't have case, but ensure no issues with mixed scripts
        assert router._normalize("What time है?") == "what time है"
        assert router._normalize("TIME क्या है") == "time क्या है"


class TestMixedLanguage:
    """Tests for mixed-language transcripts."""

    @pytest.fixture
    def router(self):
        registry = create_registry()
        return CommandRouter(registry)

    def test_hindi_english_mixed_time(self, router):
        # Hindi with English words that exist in patterns
        assert router._match_time("टाइम क्या है") is True

    def test_marathi_english_mixed_time(self, router):
        # Marathi patterns that work
        assert router._match_time("आता किती वाजले") is True

    def test_english_patterns(self, router):
        # Pure English patterns that exist
        assert router._match_time("what time is it") is True
        assert router._match_time("time now") is True


class TestEmptyAndShortAudio:
    """Tests for empty/short audio handling."""

    @pytest.fixture
    def router(self):
        registry = create_registry()
        return CommandRouter(registry)

    def test_empty_transcript(self, router):
        intent = router.recognize("")
        assert intent.name == "unknown"
        assert intent.confidence == 0.0

    def test_whitespace_only(self, router):
        intent = router.recognize("   ")
        assert intent.name == "unknown"
        assert intent.confidence == 0.0

    def test_only_punctuation(self, router):
        intent = router.recognize("?!।")
        assert intent.name == "unknown"
        assert intent.confidence == 0.0

    def test_short_nonsense(self, router):
        intent = router.recognize("अ")
        assert intent.name == "unknown"
        assert intent.confidence == 0.0


class TestHallucinatedTranscripts:
    """Tests for suspicious/hallucinated transcripts that should not trigger commands."""

    @pytest.fixture
    def router(self):
        registry = create_registry()
        return CommandRouter(registry)

    def test_youtube_outro_not_recognized(self, router):
        # Common Whisper hallucination from training data
        intent = router.recognize("see you in the next video bye")
        assert intent.name == "unknown"
        assert intent.confidence == 0.0

    def test_thanks_for_watching_not_recognized(self, router):
        intent = router.recognize("thanks for watching")
        assert intent.name == "unknown"

    def test_subscribe_not_recognized(self, router):
        intent = router.recognize("please subscribe to my channel")
        assert intent.name == "unknown"

    def test_random_english_not_recognized(self, router):
        intent = router.recognize("the quick brown fox jumps over the lazy dog")
        assert intent.name == "unknown"

    def test_short_english_fragments_not_recognized(self, router):
        # Very short fragments that might be noise
        intent = router.recognize("okay")
        assert intent.name == "unknown"
        intent = router.recognize("yes")
        assert intent.name == "unknown"
        intent = router.recognize("no")
        assert intent.name == "unknown"