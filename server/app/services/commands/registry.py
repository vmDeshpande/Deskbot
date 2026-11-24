"""Command handler registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from app.services.commands.models import CommandContext, CommandResult


class CommandHandler(ABC):
    """Base class for command handlers."""

    @property
    @abstractmethod
    def intent_name(self) -> str:
        """Unique intent name for this handler."""

    @abstractmethod
    async def execute(self, context: CommandContext) -> CommandResult:
        """Execute the command and return a result."""


class CommandRegistry:
    """Registry mapping intent names to command handlers."""

    def __init__(self) -> None:
        self._handlers: dict[str, CommandHandler] = {}

    def register(self, handler: CommandHandler) -> None:
        """Register a command handler."""
        if handler.intent_name in self._handlers:
            raise ValueError(f"Handler for intent '{handler.intent_name}' already registered")
        self._handlers[handler.intent_name] = handler

    def get(self, intent_name: str) -> CommandHandler | None:
        """Get a handler by intent name."""
        return self._handlers.get(intent_name)

    def list_intents(self) -> list[str]:
        """List all registered intent names."""
        return list(self._handlers.keys())

    async def execute(self, intent_name: str, context: CommandContext) -> CommandResult:
        """Execute a command by intent name."""
        handler = self._handlers.get(intent_name)
        if handler is None:
            return CommandResult.unknown(context, context.transcript)
        return await handler.execute(context)


def create_registry() -> CommandRegistry:
    """Create and populate the default command registry."""
    from app.services.commands.datetime_commands import TimeCommandHandler, DateCommandHandler
    from app.services.commands.assistant_commands import IdentityCommandHandler, HelpCommandHandler

    registry = CommandRegistry()
    registry.register(TimeCommandHandler())
    registry.register(DateCommandHandler())
    registry.register(IdentityCommandHandler())
    registry.register(HelpCommandHandler(registry))
    return registry