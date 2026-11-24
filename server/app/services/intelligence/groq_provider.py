"""Groq intelligence provider implementation."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from groq import AsyncGroq
from groq.types.chat import ChatCompletionMessageParam

from app.config import Settings
from app.services.intelligence.base import IntelligenceProvider
from app.services.intelligence.models import (
    FunctionDefinition,
    FunctionExecutionResult,
    IntelligenceContext,
    IntelligenceProviderInfo,
    IntentResult,
    ResponseGenerationResult,
    IntelligenceError,
    IntelligenceTimeoutError,
    IntelligenceRateLimitError,
    IntelligenceAPIError,
    IntelligenceInvalidResponseError,
)
from app.services.intelligence.tool_registry import ToolRegistry
from app.services.commands.registry import CommandRegistry
from app.services.intelligence.conversation import get_conversation_context

logger = logging.getLogger(__name__)

# Default model - using a model with good function calling support
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"

# System prompt for intent recognition
INTENT_SYSTEM_PROMPT = """You are Deskbot's intelligence layer. Analyze the user's transcribed speech and select the appropriate function to execute.

Available functions:
{function_definitions}

Rules:
1. Select ONLY from the available functions listed above
2. If the request doesn't match any function, use "none" as the intent
3. For time queries, use get_time with timezone parameter
4. For date queries, use get_date with timezone parameter
5. For identity questions, use assistant_identity
6. For help/capabilities questions, use assistant_help
7. Determine the response language from the user's input language (en, hi, mr)
8. Return valid JSON only

Response format:
{{
  "intent": "function_name_or_none",
  "arguments": {{...}},
  "response_language": "en|hi|mr",
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation"
}}"""

# System prompt for response generation
RESPONSE_SYSTEM_PROMPT = """You are Deskbot, a helpful multilingual desk assistant. Generate a natural, conversational response in the user's language.

Rules:
1. Respond in the specified language (en, hi, mr)
2. Use natural, conversational tone
3. For time/date: state the result directly without unnecessary preamble
4. For identity/help: be friendly and informative
5. Keep responses concise
6. Do not add information not in the function result
7. Return valid JSON only

Response format:
{{
  "response_text": "natural language response",
  "response_language": "en|hi|mr"
}}"""

# System prompt for conversational responses (no function)
CONVERSATIONAL_SYSTEM_PROMPT = """You are Deskbot, a helpful multilingual desk assistant. The user said something that doesn't match a specific command.

Rules:
1. Respond in the user's language (en, hi, mr)
2. Be helpful and conversational
3. If you can't do something, say so politely and suggest what you CAN do
4. Keep responses concise
5. Return valid JSON only

Response format:
{{
  "response_text": "natural language response",
  "response_language": "en|hi|mr"
}}"""


class GroqProvider(IntelligenceProvider):
    """Groq-based intelligence provider for intent recognition and response generation."""

    def __init__(self, settings: Settings, command_registry: CommandRegistry) -> None:
        self._settings = settings
        self._command_registry = command_registry
        self._tool_registry = ToolRegistry(command_registry)
        self._client: AsyncGroq | None = None
        self._model = settings.groq_model or DEFAULT_GROQ_MODEL
        self._api_key = settings.groq_api_key
        self._timeout = 30.0  # seconds
        self._max_retries = 2

    @property
    def info(self) -> IntelligenceProviderInfo:
        return IntelligenceProviderInfo(
            name="groq",
            model=self._model,
            supports_function_calling=True,
            supports_structured_output=True,
        )

    async def initialize(self) -> None:
        """Initialize the Groq client."""
        if not self._api_key:
            logger.warning("GROQ_API_KEY not configured, Groq provider will not be available")
            return

        try:
            self._client = AsyncGroq(
                api_key=self._api_key,
                timeout=self._timeout,
                max_retries=self._max_retries,
            )
            # Test the connection with a minimal request
            await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": "test"}],
                max_tokens=1,
            )
            logger.info("Groq provider initialized with model: %s", self._model)
        except Exception as exc:
            logger.error("Failed to initialize Groq provider: %s", exc)
            self._client = None
            raise IntelligenceAPIError(f"Failed to initialize Groq: {exc}") from exc

    def _build_function_definitions(self) -> list[dict[str, Any]]:
        """Build function definitions in Groq/OpenAI format."""
        definitions = self._tool_registry.get_function_definitions()
        return [
            {
                "type": "function",
                "function": {
                    "name": d.name,
                    "description": d.description,
                    "parameters": d.parameters,
                },
            }
            for d in definitions
        ]

    def _build_intent_messages(self, context: IntelligenceContext) -> list[ChatCompletionMessageParam]:
        """Build messages for intent recognition."""
        function_defs = self._tool_registry.get_function_definitions()
        func_list = "\n".join([
            f"- {f.name}: {f.description} (params: {f.parameters})"
            for f in function_defs
        ])

        system_content = INTENT_SYSTEM_PROMPT.format(function_definitions=func_list)
        
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_content},
        ]

        # Add conversation history if available
        conv_context = get_conversation_context()
        history = conv_context.get_context_for_provider(context.device_id, max_turns=3)
        for msg in history:
            messages.append(msg)

        # Add current transcript
        messages.append({
            "role": "user",
            "content": f"Transcript: \"{context.transcript}\"\nDetected language: {context.detected_language}\nLanguage confidence: {context.language_confidence}\nTimezone: {context.timezone}",
        })

        return messages

    def _build_response_messages(
        self,
        context: IntelligenceContext,
        execution_result: FunctionExecutionResult,
    ) -> list[ChatCompletionMessageParam]:
        """Build messages for response generation after function execution."""
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": RESPONSE_SYSTEM_PROMPT},
        ]

        # Add conversation history
        conv_context = get_conversation_context()
        history = conv_context.get_context_for_provider(context.device_id, max_turns=2)
        for msg in history:
            messages.append(msg)

        # Add function result context
        func_result = execution_result.result
        messages.append({
            "role": "user",
            "content": (
                f"Function executed: {execution_result.intent}\n"
                f"Arguments: {json.dumps(execution_result.arguments)}\n"
                f"Result: {json.dumps(func_result)}\n"
                f"Success: {execution_result.success}\n"
                f"User's language: {context.detected_language}\n"
                f"Target response language: {context.detected_language}"
            ),
        })

        return messages

    def _build_conversational_messages(self, context: IntelligenceContext) -> list[ChatCompletionMessageParam]:
        """Build messages for conversational response (no function)."""
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": CONVERSATIONAL_SYSTEM_PROMPT},
        ]

        # Add conversation history
        conv_context = get_conversation_context()
        history = conv_context.get_context_for_provider(context.device_id, max_turns=3)
        for msg in history:
            messages.append(msg)

        messages.append({
            "role": "user",
            "content": (
                f"User said: \"{context.transcript}\"\n"
                f"Language: {context.detected_language}\n"
                f"No matching command found. Respond conversationally in the user's language."
            ),
        })

        return messages

    async def _call_groq(
        self,
        messages: list[ChatCompletionMessageParam],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> Any:
        """Make a call to Groq API with error handling."""
        if self._client is None:
            raise IntelligenceError("Groq client not initialized", code="NOT_INITIALIZED", retryable=False)

        try:
            start_time = time.monotonic()
            kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": 500,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = tool_choice or "auto"
            if response_format:
                kwargs["response_format"] = response_format

            response = await self._client.chat.completions.create(**kwargs)
            elapsed = time.monotonic() - start_time
            logger.debug("Groq API call completed in %.2fs", elapsed)
            return response

        except Exception as exc:
            # Handle specific Groq error types
            error_msg = str(exc)
            if "rate limit" in error_msg.lower() or "429" in error_msg:
                raise IntelligenceRateLimitError(error_msg) from exc
            elif "timeout" in error_msg.lower():
                raise IntelligenceTimeoutError(error_msg) from exc
            else:
                raise IntelligenceAPIError(error_msg) from exc

    async def recognize_intent(self, context: IntelligenceContext) -> IntentResult:
        """Recognize intent from transcript using Groq."""
        if self._client is None:
            raise IntelligenceError("Groq provider not initialized", code="NOT_INITIALIZED", retryable=False)

        messages = self._build_intent_messages(context)
        tools = self._build_function_definitions()

        try:
            response = await self._call_groq(
                messages=messages,
                tools=tools,
                tool_choice="auto",
            )

            message = response.choices[0].message
            
            # Check if Groq wants to call a function
            if message.tool_calls:
                tool_call = message.tool_calls[0]
                function_name = tool_call.function.name
                try:
                    arguments = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError as exc:
                    logger.warning("Failed to parse function arguments: %s", exc)
                    arguments = {}

                # Validate function call
                valid, error = self._tool_registry.validate_function_call(function_name, arguments)
                if not valid:
                    logger.warning("Invalid function call from Groq: %s", error)
                    return IntentResult(
                        intent="none",
                        arguments={},
                        response_language=context.detected_language,
                        confidence=0.0,
                        reasoning=f"Invalid function: {error}",
                    )

                return IntentResult(
                    intent=function_name,
                    arguments=arguments,
                    response_language=context.detected_language,
                    confidence=0.9,
                    reasoning=message.content or "Function call selected",
                )

            # No function call - try to parse JSON from content
            content = message.content or "{}"
            try:
                parsed = json.loads(content)
                intent = parsed.get("intent", "none")
                arguments = parsed.get("arguments", {})
                response_language = parsed.get("response_language", context.detected_language)
                confidence = parsed.get("confidence", 0.5)
                reasoning = parsed.get("reasoning", "Parsed from content")
                
                if intent != "none":
                    valid, error = self._tool_registry.validate_function_call(intent, arguments)
                    if not valid:
                        return IntentResult(
                            intent="none",
                            arguments={},
                            response_language=response_language,
                            confidence=0.0,
                            reasoning=f"Invalid function: {error}",
                        )
                
                return IntentResult(
                    intent=intent,
                    arguments=arguments,
                    response_language=response_language,
                    confidence=confidence,
                    reasoning=reasoning,
                )
            except json.JSONDecodeError:
                # Fallback: treat as conversational
                logger.debug("Groq returned non-JSON content, treating as conversational")
                return IntentResult(
                    intent="none",
                    arguments={},
                    response_language=context.detected_language,
                    confidence=0.3,
                    reasoning="Non-structured response",
                )

        except IntelligenceError:
            raise
        except Exception as exc:
            logger.error("Intent recognition failed: %s", exc)
            raise IntelligenceAPIError(f"Intent recognition failed: {exc}") from exc

    async def generate_response(
        self,
        context: IntelligenceContext,
        execution_result: FunctionExecutionResult,
    ) -> ResponseGenerationResult:
        """Generate natural language response after function execution."""
        if self._client is None:
            raise IntelligenceError("Groq provider not initialized", code="NOT_INITIALIZED", retryable=False)

        messages = self._build_response_messages(context, execution_result)

        try:
            response = await self._call_groq(
                messages=messages,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content or "{}"
            try:
                parsed = json.loads(content)
                response_text = parsed.get("response_text", "")
                response_language = parsed.get("response_language", context.detected_language)

                if not response_text:
                    # Fallback to simple response
                    response_text = self._fallback_response(execution_result, context.detected_language)
                    response_language = context.detected_language

                return ResponseGenerationResult(
                    response_text=response_text,
                    response_language=response_language,
                    metadata={"model": self._model},
                )
            except json.JSONDecodeError:
                logger.warning("Failed to parse response JSON, using fallback")
                response_text = self._fallback_response(execution_result, context.detected_language)
                return ResponseGenerationResult(
                    response_text=response_text,
                    response_language=context.detected_language,
                    metadata={"model": self._model, "fallback": True},
                )

        except IntelligenceError:
            raise
        except Exception as exc:
            logger.error("Response generation failed: %s", exc)
            raise IntelligenceAPIError(f"Response generation failed: {exc}") from exc

    async def generate_conversational_response(
        self,
        context: IntelligenceContext,
    ) -> ResponseGenerationResult:
        """Generate conversational response when no function matches."""
        if self._client is None:
            raise IntelligenceError("Groq provider not initialized", code="NOT_INITIALIZED", retryable=False)

        messages = self._build_conversational_messages(context)

        try:
            response = await self._call_groq(
                messages=messages,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content or "{}"
            try:
                parsed = json.loads(content)
                response_text = parsed.get("response_text", "")
                response_language = parsed.get("response_language", context.detected_language)

                if not response_text:
                    response_text = self._default_conversational_response(context.detected_language)

                return ResponseGenerationResult(
                    response_text=response_text,
                    response_language=response_language,
                    metadata={"model": self._model},
                )
            except json.JSONDecodeError:
                logger.warning("Failed to parse conversational response JSON, using fallback")
                response_text = self._default_conversational_response(context.detected_language)
                return ResponseGenerationResult(
                    response_text=response_text,
                    response_language=context.detected_language,
                    metadata={"model": self._model, "fallback": True},
                )

        except IntelligenceError:
            raise
        except Exception as exc:
            logger.error("Conversational response generation failed: %s", exc)
            raise IntelligenceAPIError(f"Conversational response failed: {exc}") from exc

    def _fallback_response(self, execution_result: FunctionExecutionResult, language: str) -> str:
        """Generate a simple fallback response when Groq fails."""
        if not execution_result.success:
            return self._default_conversational_response(language)

        result = execution_result.result
        intent = execution_result.intent

        if intent == "get_time":
            iso_time = result.get("iso_time", "")
            if iso_time:
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(iso_time)
                    hour = dt.hour
                    minute = dt.minute
                    if hour == 0:
                        period = "AM"; hour_12 = 12
                    elif hour < 12:
                        period = "AM"; hour_12 = hour
                    elif hour == 12:
                        period = "PM"; hour_12 = 12
                    else:
                        period = "PM"; hour_12 = hour - 12
                    if language == "hi":
                        return f"अभी {hour_12}:{minute:02d} {period} बजे हैं।"
                    elif language == "mr":
                        return f"आता {hour_12}:{minute:02d} {period} वाजले आहेत."
                    return f"It's {hour_12}:{minute:02d} {period}."
                except Exception:
                    pass
        elif intent == "get_date":
            iso_date = result.get("iso_date", "")
            if iso_date:
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(iso_date)
                    if language == "hi":
                        return f"आज {dt.strftime('%A, %B %d, %Y')} है।"
                    elif language == "mr":
                        return f"आज {dt.strftime('%A, %B %d, %Y')} आहे."
                    return f"Today is {dt.strftime('%A, %B %d, %Y')}."
                except Exception:
                    pass

        return self._default_conversational_response(language)

    def _default_conversational_response(self, language: str) -> str:
        """Default conversational response for unknown requests."""
        responses = {
            "en": "I'm sorry, I can't help with that yet. You can ask me about the time, date, or about myself.",
            "hi": "माफ़ कीजिए, मैं अभी यह काम नहीं कर सकता। आप समय, तारीख या मेरे बारे में पूछ सकते हैं।",
            "mr": "माफ करा, मी अजून हे करू शकत नाही. तुम्ही मला वेळ, तारीख किंवा माझ्याबद्दल विचारू शकता.",
        }
        return responses.get(language, responses["hi"])

    async def shutdown(self) -> None:
        """Clean up Groq client."""
        if self._client is not None:
            await self._client.close()
            self._client = None
        logger.info("Groq provider shut down")