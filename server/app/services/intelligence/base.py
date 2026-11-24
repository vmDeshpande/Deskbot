"""Base intelligence provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from app.services.intelligence.models import (
    FunctionDefinition,
    FunctionExecutionResult,
    IntelligenceContext,
    IntelligenceProviderInfo,
    IntentResult,
    ResponseGenerationResult,
)


class IntelligenceProvider(ABC):
    """Abstract base class for intelligence providers."""

    @property
    @abstractmethod
    def info(self) -> IntelligenceProviderInfo:
        """Return provider information."""

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the provider (load config, verify API access, etc.)."""

    @abstractmethod
    async def recognize_intent(self, context: IntelligenceContext) -> IntentResult:
        """
        Analyze transcript and select appropriate function with arguments.
        
        Args:
            context: Full context including transcript, language, available functions
            
        Returns:
            Structured intent with function name, arguments, and response language
        """

    @abstractmethod
    async def generate_response(
        self,
        context: IntelligenceContext,
        execution_result: FunctionExecutionResult,
    ) -> ResponseGenerationResult:
        """
        Generate natural language response after function execution.
        
        Args:
            context: Original request context
            execution_result: Result from function execution
            
        Returns:
            Natural language response in the appropriate language
        """

    @abstractmethod
    async def generate_conversational_response(
        self,
        context: IntelligenceContext,
    ) -> ResponseGenerationResult:
        """
        Generate a conversational response without function execution.
        
        Used for general conversation, unsupported requests, or when no function matches.
        
        Args:
            context: Original request context
            
        Returns:
            Natural language response in the appropriate language
        """

    @abstractmethod
    async def shutdown(self) -> None:
        """Clean up resources."""

    def get_available_functions(self) -> list[FunctionDefinition]:
        """Get list of available function definitions. Override to provide custom functions."""
        return []