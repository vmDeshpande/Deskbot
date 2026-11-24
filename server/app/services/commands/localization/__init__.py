"""Localization package for command responses."""

from __future__ import annotations

from datetime import datetime
from types import ModuleType

# Import localization modules
from app.services.commands.localization import en, hi, mr

_LANG_MODULES: dict[str, ModuleType] = {
    "en": en,
    "hi": hi,
    "mr": mr,
}


def get_localization(language: str) -> ModuleType:
    """Get localization module for a language code."""
    # Normalize language code
    lang = language.lower().strip()
    if lang.startswith("en"):
        return en
    elif lang.startswith("hi"):
        return hi
    elif lang.startswith("mr"):
        return mr
    # Default to Hindi
    return hi


def format_time(language: str, dt: datetime) -> str:
    """Format time for a language."""
    loc = get_localization(language)
    return loc.TIME["response"](dt)


def format_date(language: str, dt: datetime) -> str:
    """Format date for a language."""
    loc = get_localization(language)
    return loc.DATE["response"](dt)


def get_identity_response(language: str) -> str:
    """Get identity response for a language."""
    loc = get_localization(language)
    return loc.IDENTITY["response"]


def get_help_response(language: str) -> str:
    """Get help response for a language."""
    loc = get_localization(language)
    lines = [loc.HELP["intro"]]
    for intent in ["get_time", "get_date", "assistant_identity", "assistant_help"]:
        desc = loc.INTENT_DESCRIPTIONS.get(intent, intent)
        lines.append(f"• {desc}")
    return "\n".join(lines)


def get_unknown_response(language: str) -> str:
    """Get unknown command response for a language."""
    loc = get_localization(language)
    return loc.UNKNOWN["response"]