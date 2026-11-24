"""English localization strings."""

from datetime import datetime
from zoneinfo import ZoneInfo


def _get_timezone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def _format_time_english(dt: datetime) -> str:
    hour = dt.hour
    minute = dt.minute

    if hour == 0:
        period = "AM"
        hour_12 = 12
    elif hour < 12:
        period = "AM"
        hour_12 = hour
    elif hour == 12:
        period = "PM"
        hour_12 = 12
    else:
        period = "PM"
        hour_12 = hour - 12

    if minute == 0:
        return f"It's {hour_12} {period}."
    else:
        return f"It's {hour_12}:{minute:02d} {period}."


def _format_date_english(dt: datetime) -> str:
    weekdays = [
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
    ]
    months = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ]
    weekday = weekdays[dt.weekday()]
    month = months[dt.month - 1]
    return f"Today is {weekday}, {month} {dt.day}, {dt.year}."


TIME = {
    "response": _format_time_english,
}

DATE = {
    "response": _format_date_english,
}

IDENTITY = {
    "response": "I'm Deskbot, your smart desk assistant.",
}

HELP = {
    "intro": "I can help you with:",
    "get_time": "Ask for the current time",
    "get_date": "Ask for today's date",
    "assistant_identity": "Ask who I am",
    "assistant_help": "Show this help",
}

UNKNOWN = {
    "response": "I'm sorry, I can't do that yet. You can ask me about the time, date, or about myself.",
}

INTENT_DESCRIPTIONS = {
    "get_time": "Ask for the current time",
    "get_date": "Ask for today's date",
    "assistant_identity": "Ask who I am",
    "assistant_help": "Show this help",
}