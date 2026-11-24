"""Command router for intent recognition."""

from __future__ import annotations

import re
from typing import Callable

from app.services.commands.models import CommandIntent, CommandContext
from app.services.commands.registry import CommandRegistry


class CommandRouter:
    """Routes transcripts to command intents using keyword matching."""

    def __init__(self, registry: CommandRegistry) -> None:
        self._registry = registry
        self._patterns: list[tuple[str, Callable[[str], bool]]] = [
            ("get_time", self._match_time),
            ("get_date", self._match_date),
            ("assistant_identity", self._match_identity),
            ("assistant_help", self._match_help),
        ]

    def _normalize(self, text: str) -> str:
        """Normalize text for matching: lowercase, collapse whitespace, remove punctuation."""
        text = text.strip().lower()
        text = re.sub(r"[।.?!,\"'।]", "", text)  # Remove punctuation including Devanagari danda
        text = re.sub(r"\s+", " ", text)  # Collapse whitespace
        return text

    def _match_time(self, text: str) -> bool:
        """Match time-related queries."""
        # Hindi patterns (without word boundaries for Devanagari compatibility)
        hindi_patterns = [
            r"अभी\s+कितने\s+बजे",
            r"समय\s+क्या",
            r"अभी\s+का\s+समय",
            r"कितना\s+समय\s+हुआ",
            r"समय\s+बताओ",
            r"टाइम\s+क्या",
            r"कितने\s+बजे",
            r"अभी\s+क्या\s+समय\s+हो\s+रहा",
            r"वर्तमान\s+समय",
        ]
        # English patterns (with word boundaries)
        english_patterns = [
            r"\bwhat\s+time\s+is\s+it\b",
            r"\btell\s+me\s+the\s+current\s+time\b",
            r"\bwhat'?s\s+the\s+time\b",
            r"\bcurrent\s+time\b",
            r"\btime\s+now\b",
            r"\bwhat'?s\s+the\s+current\s+time\b",
        ]
        # Marathi patterns
        marathi_patterns = [
            r"आता\s+किती\s+वाजले",
            r"वेळ\s+किती",
            r"आता\s+वेळ\s+काय",
            r"किती\s+वाजले",
            r"वेळ\s+सांगा",
            r"आत्ता\s+किती\s+वाजले\s+आहेत",
            r"आता\s+किती\s+वाजले\s+आहे",
            r"वर्तमान\s+वेळ",
        ]
        for pattern in hindi_patterns:
            if re.search(pattern, text):
                return True
        for pattern in english_patterns:
            if re.search(pattern, text):
                return True
        for pattern in marathi_patterns:
            if re.search(pattern, text):
                return True
        return False

    def _match_date(self, text: str) -> bool:
        """Match date-related queries."""
        hindi_patterns = [
            r"आज\s+कौन\s+सी\s+तारीख",
            r"आज\s+की\s+तारीख",
            r"आज\s+कौन\s+सा\s+दिन",
            r"तारीख\s+क्या\s+है",
            r"आज\s+क्या\s+है",
            r"आज\s+क्या\s+दिन\s+है",
        ]
        english_patterns = [
            r"\bwhat'?s\s+today'?s\s+date\b",
            r"\btell\s+me\s+today'?s\s+date\b",
            r"\bwhat\s+day\s+is\s+it\s+today\b",
            r"\btoday'?s\s+date\b",
            r"\bwhat\s+is\s+the\s+date\b",
            r"\bwhat'?s\s+the\s+date\s+today\b",
        ]
        marathi_patterns = [
            r"आज\s+कोणती\s+तारीख",
            r"आजची\s+तारीख",
            r"आज\s+कोणता\s+दिवस",
            r"तारीख\s+काय",
            r"आज\s+काय",
            r"आज\s+किती\s+तारीख\s+आहे",
            r"आजची\s+तारीख\s+काय\s+आहे",
        ]
        for pattern in hindi_patterns:
            if re.search(pattern, text):
                return True
        for pattern in english_patterns:
            if re.search(pattern, text):
                return True
        for pattern in marathi_patterns:
            if re.search(pattern, text):
                return True
        return False

    def _match_identity(self, text: str) -> bool:
        """Match identity-related queries."""
        hindi_patterns = [
            r"तुम\s+कौन\s+हो",
            r"तुम्हारा\s+नाम\s+क्या",
            r"तुम\s+क्या\s+हो",
            r"आप\s+कौन\s+हैं",
            r"आपका\s+नाम\s+क्या",
            r"तुम्हारा\s+परिचय",
        ]
        english_patterns = [
            r"\bwho\s+are\s+you\b",
            r"\bwhat'?s\s+your\s+name\b",
            r"\bwhat\s+are\s+you\b",
            r"\byour\s+name\b",
            r"\btell\s+me\s+about\s+yourself\b",
        ]
        marathi_patterns = [
            r"तू\s+कोण\s+आहेस",
            r"तुझं\s+नाव\s+काय",
            r"तुम्ही\s+कोण\s+आहात",
            r"तुमचे\s+नाव\s+काय",
            r"तुम्ही\s+कोण",
        ]
        for pattern in hindi_patterns:
            if re.search(pattern, text):
                return True
        for pattern in english_patterns:
            if re.search(pattern, text):
                return True
        for pattern in marathi_patterns:
            if re.search(pattern, text):
                return True
        return False

    def _match_help(self, text: str) -> bool:
        """Match help/capabilities queries."""
        hindi_patterns = [
            r"तुम\s+क्या\s+कर\s+सकते\s+हो",
            r"तुम्हारे\s+पास\s+कौन[-\s]कौन\s+से\s+फीचर्स",
            r"मुझे\s+क्या\s+पूछना\s+चाहिए",
            r"क्या\s+कर\s+सकते\s+हो",
            r"कमांड\s+क्या\s+हैं",
            r"मदद\s+करो",
            r"क्या\s+क्या\s+कर\s+सकते\s+हो",
        ]
        english_patterns = [
            r"\bwhat\s+can\s+you\s+do\b",
            r"\bwhat\s+commands\s+do\s+you\s+support\b",
            r"\bhelp\s+me\b",
            r"\bcapabilities\b",
            r"\bwhat\s+are\s+your\s+features\b",
            r"\bwhat\s+should\s+i\s+ask\b",
        ]
        marathi_patterns = [
            r"तू\s+काय\s+करू\s+शकतो",
            r"तुम्ही\s+काय\s+करू\s+शकता",
            r"मला\s+काय\s+विचारायचे",
            r"कमांड\s+काय",
            r"मदत",
            r"काय\s+काय\s+करू\s+शकता",
        ]
        for pattern in hindi_patterns:
            if re.search(pattern, text):
                return True
        for pattern in english_patterns:
            if re.search(pattern, text):
                return True
        for pattern in marathi_patterns:
            if re.search(pattern, text):
                return True
        return False

    def recognize(self, transcript: str) -> CommandIntent:
        """Recognize intent from transcript text."""
        normalized = self._normalize(transcript)
        if not normalized:
            return CommandIntent(name="unknown", confidence=0.0)

        for intent_name, matcher in self._patterns:
            if matcher(normalized):
                return CommandIntent(name=intent_name, confidence=1.0)

        return CommandIntent(name="unknown", confidence=0.0)

    async def route(self, context: CommandContext) -> CommandIntent:
        """Route transcript to intent and return recognized intent."""
        return self.recognize(context.transcript)