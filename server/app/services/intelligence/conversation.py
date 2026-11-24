"""Conversation context management for intelligence provider."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


@dataclass
class ConversationTurn:
    """A single turn in the conversation."""

    recording_id: str
    transcript: str
    detected_language: str
    intent: str | None
    recognized: bool
    response_text: str
    response_language: str
    timestamp: float


class ConversationContext:
    """
    Manages conversation context for the intelligence provider.
    
    Keeps a bounded history of recent turns to provide context
    for follow-up questions and conversational continuity.
    """

    def __init__(self, max_turns: int = 5) -> None:
        self._max_turns = max_turns
        self._history: deque[ConversationTurn] = deque(maxlen=max_turns)
        self._device_contexts: dict[str, deque[ConversationTurn]] = {}

    def add_turn(
        self,
        device_id: str,
        recording_id: str,
        transcript: str,
        detected_language: str,
        intent: str | None,
        recognized: bool,
        response_text: str,
        response_language: str,
        timestamp: float,
    ) -> None:
        """Add a conversation turn to history."""
        turn = ConversationTurn(
            recording_id=recording_id,
            transcript=transcript,
            detected_language=detected_language,
            intent=intent,
            recognized=recognized,
            response_text=response_text,
            response_language=response_language,
            timestamp=timestamp,
        )
        self._history.append(turn)
        
        if device_id not in self._device_contexts:
            self._device_contexts[device_id] = deque(maxlen=self._max_turns)
        self._device_contexts[device_id].append(turn)

    def get_history(self, device_id: str | None = None, max_turns: int | None = None) -> list[ConversationTurn]:
        """Get conversation history, optionally filtered by device."""
        if device_id is not None:
            history = self._device_contexts.get(device_id, deque())
        else:
            history = self._history
        
        turns = list(history)
        if max_turns is not None:
            turns = turns[-max_turns:]
        return turns

    def get_context_for_provider(self, device_id: str | None = None, max_turns: int = 3) -> list[dict[str, str]]:
        """
        Get formatted conversation history for intelligence provider.
        
        Returns a list of message dicts with 'role' and 'content' keys
        suitable for LLM context.
        """
        history = self.get_history(device_id, max_turns)
        messages = []
        
        for turn in history:
            # Add user message
            messages.append({
                "role": "user",
                "content": turn.transcript,
            })
            # Add assistant response if it was recognized
            if turn.recognized and turn.response_text:
                messages.append({
                    "role": "assistant",
                    "content": turn.response_text,
                })
        
        return messages

    def clear_device_history(self, device_id: str) -> None:
        """Clear history for a specific device."""
        if device_id in self._device_contexts:
            self._device_contexts[device_id].clear()

    def clear_all(self) -> None:
        """Clear all conversation history."""
        self._history.clear()
        self._device_contexts.clear()


# Global conversation context instance
_conversation_context: ConversationContext | None = None


def get_conversation_context() -> ConversationContext:
    """Get or create the global conversation context."""
    global _conversation_context
    if _conversation_context is None:
        _conversation_context = ConversationContext()
    return _conversation_context