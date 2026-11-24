"""Intelligence provider factory."""

from __future__ import annotations

import logging
from typing import Any

from app.config import Settings
from app.services.commands.registry import CommandRegistry
from app.services.intelligence.base import IntelligenceProvider
from app.services.intelligence.models import IntelligenceProviderInfo
from app.services.intelligence.groq_provider import GroqProvider

logger = logging.getLogger(__name__)


class IntelligenceProviderFactory:
    """Factory for creating intelligence providers based on configuration."""

    def __init__(self, settings: Settings, command_registry: CommandRegistry) -> None:
        self._settings = settings
        self._command_registry = command_registry
        self._provider: IntelligenceProvider | None = None

    async def get_provider(self) -> IntelligenceProvider | None:
        """Get or create the configured intelligence provider."""
        if self._provider is not None:
            return self._provider

        if not self._settings.intelligence_enabled:
            logger.info("Intelligence provider disabled via configuration")
            return None

        provider_name = self._settings.intelligence_provider.lower()

        if provider_name == "groq":
            logger.info("Creating Groq intelligence provider")
            self._provider = GroqProvider(self._settings, self._command_registry)
        else:
            logger.warning(
                "Unknown intelligence provider '%s', intelligence disabled",
                provider_name,
            )
            return None

        try:
            await self._provider.initialize()
            return self._provider
        except Exception as exc:
            logger.error("Failed to initialize intelligence provider: %s", exc)
            self._provider = None
            return None

    async def shutdown(self) -> None:
        """Shutdown the current provider."""
        if self._provider is not None:
            await self._provider.shutdown()
            self._provider = None

    def get_provider_info(self) -> IntelligenceProviderInfo | None:
        """Get info about the current provider without initializing."""
        if self._provider is not None:
            return self._provider.info
        return None