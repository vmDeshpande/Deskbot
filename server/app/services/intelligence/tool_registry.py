"""Tool registry for intelligence provider function calling."""

from __future__ import annotations

from typing import Any, Callable, Awaitable

from app.services.commands.models import CommandContext, CommandResult
from app.services.commands.registry import CommandRegistry
from app.services.intelligence.models import FunctionDefinition


class ToolRegistry:
    """Registry mapping intelligence function names to command handlers."""

    def __init__(self, command_registry: CommandRegistry) -> None:
        self._command_registry = command_registry
        self._function_map: dict[str, str] = {
            "get_time": "get_time",
            "get_date": "get_date",
            "assistant_identity": "assistant_identity",
            "assistant_help": "assistant_help",
        }
        self._function_descriptions = {
            "get_time": "Get the current time in the specified timezone",
            "get_date": "Get today's date in the specified timezone",
            "assistant_identity": "Get information about the assistant's identity",
            "assistant_help": "Get a list of available commands and capabilities",
        }

    def get_function_definitions(self) -> list[FunctionDefinition]:
        """Get all function definitions for the intelligence provider."""
        definitions = []
        for func_name, intent_name in self._function_map.items():
            if self._command_registry.get(intent_name) is not None:
                definitions.append(
                    FunctionDefinition(
                        name=func_name,
                        description=self._function_descriptions.get(func_name, ""),
                        parameters=self._get_function_parameters(func_name),
                        required=self._get_required_parameters(func_name),
                    )
                )
        return definitions

    def _get_function_parameters(self, func_name: str) -> dict[str, Any]:
        """Get JSON schema parameters for a function."""
        if func_name == "get_time":
            return {
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone identifier (e.g., 'Asia/Kolkata', 'America/New_York')",
                        "default": "Asia/Kolkata",
                    }
                },
                "additionalProperties": False,
            }
        elif func_name == "get_date":
            return {
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone identifier (e.g., 'Asia/Kolkata', 'America/New_York')",
                        "default": "Asia/Kolkata",
                    }
                },
                "additionalProperties": False,
            }
        elif func_name in ("assistant_identity", "assistant_help"):
            return {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            }
        return {"type": "object", "properties": {}, "additionalProperties": False}

    def _get_required_parameters(self, func_name: str) -> list[str]:
        """Get required parameter names for a function."""
        if func_name in ("get_time", "get_date"):
            return []  # timezone has default
        return []

    def validate_function_call(self, function_name: str, arguments: dict[str, Any]) -> tuple[bool, str | None]:
        """Validate a function call from the intelligence provider."""
        if function_name not in self._function_map:
            return False, f"Unknown function: {function_name}"

        intent_name = self._function_map[function_name]
        if self._command_registry.get(intent_name) is None:
            return False, f"Function not available: {function_name}"

        # Validate arguments based on function
        if function_name in ("get_time", "get_date"):
            timezone = arguments.get("timezone")
            if timezone is not None and not isinstance(timezone, str):
                return False, "timezone must be a string"
            # Could validate IANA timezone format here if needed

        return True, None

    async def execute_function(
        self,
        function_name: str,
        arguments: dict[str, Any],
        context: CommandContext,
    ) -> CommandResult:
        """Execute a registered function and return the result."""
        intent_name = self._function_map.get(function_name)
        if intent_name is None:
            raise ValueError(f"Unknown function: {function_name}")

        handler = self._command_registry.get(intent_name)
        if handler is None:
            raise ValueError(f"Handler not found for intent: {intent_name}")

        # Update context with function arguments if needed
        # For get_time/get_date, the timezone comes from context settings
        # The function arguments are mainly for the LLM to specify intent
        return await handler.execute(context)

    def list_functions(self) -> list[str]:
        """List available function names."""
        return [name for name in self._function_map if self._command_registry.get(self._function_map[name]) is not None]

    def get_intent_name(self, function_name: str) -> str | None:
        """Get the command registry intent name for a function."""
        return self._function_map.get(function_name)