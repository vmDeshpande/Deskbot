"""Tests for intelligence tool registry."""

from __future__ import annotations

import pytest

from app.services.commands.registry import create_registry
from app.services.intelligence.tool_registry import ToolRegistry


class TestToolRegistry:
    @pytest.fixture
    def registry(self):
        command_registry = create_registry()
        return ToolRegistry(command_registry)

    def test_get_function_definitions(self, registry):
        definitions = registry.get_function_definitions()
        assert len(definitions) == 4
        names = {d.name for d in definitions}
        assert names == {"get_time", "get_date", "assistant_identity", "assistant_help"}

    def test_get_time_parameters(self, registry):
        definitions = registry.get_function_definitions()
        time_def = next(d for d in definitions if d.name == "get_time")
        assert time_def.name == "get_time"
        assert "timezone" in time_def.parameters["properties"]
        assert time_def.required == []

    def test_get_date_parameters(self, registry):
        definitions = registry.get_function_definitions()
        date_def = next(d for d in definitions if d.name == "get_date")
        assert date_def.name == "get_date"
        assert "timezone" in date_def.parameters["properties"]

    def test_assistant_identity_no_params(self, registry):
        definitions = registry.get_function_definitions()
        id_def = next(d for d in definitions if d.name == "assistant_identity")
        assert id_def.name == "assistant_identity"
        assert id_def.parameters["properties"] == {}

    def test_assistant_help_no_params(self, registry):
        definitions = registry.get_function_definitions()
        help_def = next(d for d in definitions if d.name == "assistant_help")
        assert help_def.name == "assistant_help"
        assert help_def.parameters["properties"] == {}

    def test_validate_function_call_valid(self, registry):
        valid, error = registry.validate_function_call("get_time", {"timezone": "Asia/Kolkata"})
        assert valid is True
        assert error is None

    def test_validate_function_call_unknown(self, registry):
        valid, error = registry.validate_function_call("unknown_function", {})
        assert valid is False
        assert "Unknown function" in error

    def test_validate_function_call_invalid_timezone_type(self, registry):
        valid, error = registry.validate_function_call("get_time", {"timezone": 123})
        assert valid is False
        assert "timezone must be a string" in error

    def test_validate_function_call_optional_timezone(self, registry):
        valid, error = registry.validate_function_call("get_time", {})
        assert valid is True
        assert error is None

    def test_list_functions(self, registry):
        functions = registry.list_functions()
        assert set(functions) == {"get_time", "get_date", "assistant_identity", "assistant_help"}

    def test_get_intent_name(self, registry):
        assert registry.get_intent_name("get_time") == "get_time"
        assert registry.get_intent_name("get_date") == "get_date"
        assert registry.get_intent_name("assistant_identity") == "assistant_identity"
        assert registry.get_intent_name("assistant_help") == "assistant_help"
        assert registry.get_intent_name("unknown") is None

    @pytest.mark.asyncio
    async def test_execute_get_time(self, registry):
        from app.services.commands.models import CommandContext
        from uuid import uuid4
        
        context = CommandContext(
            device_id="test-device",
            recording_id=uuid4(),
            transcript="What time is it?",
            language="en-US",
            detected_language="en",
            language_confidence=0.9,
            timezone="Asia/Kolkata",
            location="Nashik, India",
        )
        
        result = await registry.execute_function("get_time", {"timezone": "Asia/Kolkata"}, context)
        assert result.recognized is True
        assert result.intent.name == "get_time"
        assert "PM" in result.response_text or "AM" in result.response_text

    @pytest.mark.asyncio
    async def test_execute_get_date(self, registry):
        from app.services.commands.models import CommandContext
        from uuid import uuid4
        
        context = CommandContext(
            device_id="test-device",
            recording_id=uuid4(),
            transcript="What's today's date?",
            language="en-US",
            detected_language="en",
            language_confidence=0.9,
            timezone="Asia/Kolkata",
            location="Nashik, India",
        )
        
        result = await registry.execute_function("get_date", {}, context)
        assert result.recognized is True
        assert result.intent.name == "get_date"
        assert "Today is" in result.response_text

    @pytest.mark.asyncio
    async def test_execute_identity(self, registry):
        from app.services.commands.models import CommandContext
        from uuid import uuid4
        
        context = CommandContext(
            device_id="test-device",
            recording_id=uuid4(),
            transcript="Who are you?",
            language="en-US",
            detected_language="en",
            language_confidence=0.9,
            timezone="Asia/Kolkata",
            location="Nashik, India",
        )
        
        result = await registry.execute_function("assistant_identity", {}, context)
        assert result.recognized is True
        assert result.intent.name == "assistant_identity"
        assert "Deskbot" in result.response_text

    @pytest.mark.asyncio
    async def test_execute_help(self, registry):
        from app.services.commands.models import CommandContext
        from uuid import uuid4
        
        context = CommandContext(
            device_id="test-device",
            recording_id=uuid4(),
            transcript="What can you do?",
            language="en-US",
            detected_language="en",
            language_confidence=0.9,
            timezone="Asia/Kolkata",
            location="Nashik, India",
        )
        
        result = await registry.execute_function("assistant_help", {}, context)
        assert result.recognized is True
        assert result.intent.name == "assistant_help"
        assert "time" in result.response_text.lower()