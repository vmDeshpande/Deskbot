"""Tests for intelligence provider factory and conversation context."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.config import Settings
from app.services.commands.registry import create_registry
from app.services.intelligence import (
    IntelligenceProviderFactory,
    get_conversation_context,
)
from app.services.intelligence.conversation import ConversationContext
from app.services.intelligence.models import ConversationTurn


class TestIntelligenceProviderFactory:
    @pytest.fixture
    def settings_enabled(self):
        return Settings(
            _env_file=None,
            groq_api_key="test-api-key",
            groq_model="llama-3.3-70b-versatile",
            intelligence_enabled=True,
            intelligence_provider="groq",
        )

    @pytest.fixture
    def settings_disabled(self):
        return Settings(
            _env_file=None,
            groq_api_key="test-api-key",
            intelligence_enabled=False,
        )

    @pytest.fixture
    def settings_unknown_provider(self):
        return Settings(
            _env_file=None,
            groq_api_key="test-api-key",
            intelligence_enabled=True,
            intelligence_provider="unknown",
        )

    @pytest.fixture
    def command_registry(self):
        return create_registry()

    @pytest.mark.asyncio
    async def test_factory_creates_groq_provider(self, settings_enabled, command_registry):
        factory = IntelligenceProviderFactory(settings_enabled, command_registry)
        
        with patch("app.services.intelligence.factory.GroqProvider") as mock_provider_class:
            mock_provider = AsyncMock()
            mock_provider.initialize = AsyncMock()
            mock_provider.info.name = "groq"
            mock_provider_class.return_value = mock_provider
            
            provider = await factory.get_provider()
            
            assert provider is not None
            mock_provider_class.assert_called_once()
            mock_provider.initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_factory_returns_none_when_disabled(self, settings_disabled, command_registry):
        factory = IntelligenceProviderFactory(settings_disabled, command_registry)
        provider = await factory.get_provider()
        assert provider is None

    @pytest.mark.asyncio
    async def test_factory_returns_none_for_unknown_provider(self, settings_unknown_provider, command_registry):
        factory = IntelligenceProviderFactory(settings_unknown_provider, command_registry)
        provider = await factory.get_provider()
        assert provider is None

    @pytest.mark.asyncio
    async def test_factory_caches_provider(self, settings_enabled, command_registry):
        factory = IntelligenceProviderFactory(settings_enabled, command_registry)
        
        with patch("app.services.intelligence.factory.GroqProvider") as mock_provider_class:
            mock_provider = AsyncMock()
            mock_provider.initialize = AsyncMock()
            mock_provider.info.name = "groq"
            mock_provider_class.return_value = mock_provider
            
            provider1 = await factory.get_provider()
            provider2 = await factory.get_provider()
            
            assert provider1 is provider2
            mock_provider_class.assert_called_once()

    @pytest.mark.asyncio
    async def test_factory_shutdown(self, settings_enabled, command_registry):
        factory = IntelligenceProviderFactory(settings_enabled, command_registry)
        
        with patch("app.services.intelligence.factory.GroqProvider") as mock_provider_class:
            mock_provider = AsyncMock()
            mock_provider.initialize = AsyncMock()
            mock_provider.shutdown = AsyncMock()
            mock_provider.info.name = "groq"
            mock_provider_class.return_value = mock_provider
            
            await factory.get_provider()
            await factory.shutdown()
            
            mock_provider.shutdown.assert_called_once()

    def test_get_provider_info_before_init(self, settings_enabled, command_registry):
        factory = IntelligenceProviderFactory(settings_enabled, command_registry)
        info = factory.get_provider_info()
        assert info is None


class TestConversationContextGlobal:
    def test_get_conversation_context_returns_same_instance(self):
        ctx1 = get_conversation_context()
        ctx2 = get_conversation_context()
        assert ctx1 is ctx2

    def test_conversation_context_isolation(self):
        ctx = get_conversation_context()
        ctx.clear_all()
        
        # Add turns for different devices
        turn1 = ConversationTurn(
            recording_id="rec-1",
            transcript="Hello",
            detected_language="en",
            intent=None,
            recognized=False,
            response_text="Hi",
            response_language="en",
            timestamp=1000.0,
        )
        turn2 = ConversationTurn(
            recording_id="rec-2",
            transcript="Time?",
            detected_language="en",
            intent="get_time",
            recognized=True,
            response_text="2 PM",
            response_language="en",
            timestamp=2000.0,
        )
        
        ctx.add_turn("device-1", turn1.recording_id, turn1.transcript, turn1.detected_language,
                    turn1.intent, turn1.recognized, turn1.response_text, turn1.response_language, turn1.timestamp)
        ctx.add_turn("device-1", turn2.recording_id, turn2.transcript, turn2.detected_language,
                    turn2.intent, turn2.recognized, turn2.response_text, turn2.response_language, turn2.timestamp)
        
        history_device1 = ctx.get_history("device-1")
        history_device2 = ctx.get_history("device-2")
        
        assert len(history_device1) == 2
        assert len(history_device2) == 0

    def test_clear_device_history(self):
        ctx = get_conversation_context()
        ctx.clear_all()
        
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
        ctx.add_turn("device-1", turn.recording_id, turn.transcript, turn.detected_language,
                    turn.intent, turn.recognized, turn.response_text, turn.response_language, turn.timestamp)
        
        ctx.clear_device_history("device-1")
        assert ctx.get_history("device-1") == []
        
        # Other device history should be unaffected
        ctx.add_turn("device-2", turn.recording_id, turn.transcript, turn.detected_language,
                    turn.intent, turn.recognized, turn.response_text, turn.response_language, turn.timestamp)
        ctx.clear_device_history("device-1")
        assert len(ctx.get_history("device-2")) == 1

    def test_get_context_for_provider_format(self):
        ctx = get_conversation_context()
        ctx.clear_all()
        
        turn1 = ConversationTurn(
            recording_id="rec-1",
            transcript="What time is it?",
            detected_language="en",
            intent="get_time",
            recognized=True,
            response_text="It's 2 PM.",
            response_language="en",
            timestamp=1000.0,
        )
        turn2 = ConversationTurn(
            recording_id="rec-2",
            transcript="What's the date?",
            detected_language="en",
            intent="get_date",
            recognized=True,
            response_text="Today is Monday.",
            response_language="en",
            timestamp=2000.0,
        )
        
        ctx.add_turn("device-1", turn1.recording_id, turn1.transcript, turn1.detected_language,
                    turn1.intent, turn1.recognized, turn1.response_text, turn1.response_language, turn1.timestamp)
        ctx.add_turn("device-1", turn2.recording_id, turn2.transcript, turn2.detected_language,
                    turn2.intent, turn2.recognized, turn2.response_text, turn2.response_language, turn2.timestamp)
        
        provider_context = ctx.get_context_for_provider("device-1", max_turns=2)
        
        assert len(provider_context) == 4  # 2 user + 2 assistant
        assert provider_context[0]["role"] == "user"
        assert provider_context[0]["content"] == "What time is it?"
        assert provider_context[1]["role"] == "assistant"
        assert provider_context[1]["content"] == "It's 2 PM."
        assert provider_context[2]["role"] == "user"
        assert provider_context[2]["content"] == "What's the date?"
        assert provider_context[3]["role"] == "assistant"
        assert provider_context[3]["content"] == "Today is Monday."

    def test_max_turns_limit_in_provider_context(self):
        ctx = get_conversation_context()
        ctx.clear_all()
        
        for i in range(5):
            turn = ConversationTurn(
                recording_id=f"rec-{i}",
                transcript=f"Message {i}",
                detected_language="en",
                intent="get_time" if i % 2 == 0 else "get_date",
                recognized=True,
                response_text=f"Response {i}",
                response_language="en",
                timestamp=float(i),
            )
            ctx.add_turn("device-1", turn.recording_id, turn.transcript, turn.detected_language,
                        turn.intent, turn.recognized, turn.response_text, turn.response_language, turn.timestamp)
        
        provider_context = ctx.get_context_for_provider("device-1", max_turns=2)
        # 2 turns = 4 messages (2 user + 2 assistant)
        assert len(provider_context) == 4