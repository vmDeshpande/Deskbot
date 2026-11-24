"""Assistant identity and help command handlers."""

from __future__ import annotations

from app.services.commands.localization import (
    get_identity_response,
    get_help_response,
    get_unknown_response,
)
from app.services.commands.models import CommandContext, CommandIntent, CommandResult
from app.services.commands.registry import CommandHandler, CommandRegistry


class IdentityCommandHandler(CommandHandler):
    """Handler for assistant identity queries."""

    @property
    def intent_name(self) -> str:
        return "assistant_identity"

    async def execute(self, context: CommandContext) -> CommandResult:
        response_text = get_identity_response(context.detected_language)
        return CommandResult(
            intent=CommandIntent(name=self.intent_name),
            recognized=True,
            response_text=response_text,
            response_language=context.detected_language,
            metadata={},
        )


class HelpCommandHandler(CommandHandler):
    """Handler for help/capabilities queries."""

    def __init__(self, registry: CommandRegistry) -> None:
        self._registry = registry

    @property
    def intent_name(self) -> str:
        return "assistant_help"

    async def execute(self, context: CommandContext) -> CommandResult:
        response_text = get_help_response(context.detected_language)
        return CommandResult(
            intent=CommandIntent(name=self.intent_name),
            recognized=True,
            response_text=response_text,
            response_language=context.detected_language,
            metadata={"supported_intents": self._registry.list_intents()},
        )