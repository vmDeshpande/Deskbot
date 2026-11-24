"""Marathi localization strings."""

from datetime import datetime
from zoneinfo import ZoneInfo


def _get_timezone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def _format_time_marathi(dt: datetime) -> str:
    hour = dt.hour
    minute = dt.minute
    period = ""

    if hour == 0:
        period = "रात्री"
        hour_12 = 12
    elif hour < 12:
        period = "सकाळी"
        hour_12 = hour
    elif hour == 12:
        period = "दुपारी"
        hour_12 = 12
    elif hour < 17:
        period = "दुपारी"
        hour_12 = hour - 12
    elif hour < 20:
        period = "संध्याकाळी"
        hour_12 = hour - 12
    else:
        period = "रात्री"
        hour_12 = hour - 12

    marathi_digits = "०१२३४५६७८९"
    hour_str = "".join(marathi_digits[int(d)] for d in str(hour_12))
    minute_str = "".join(marathi_digits[int(d)] for d in str(minute).zfill(2))

    if minute == 0:
        return f"आता {period}चे {hour_str} वाजले आहेत."
    else:
        return f"आता {period}चे {hour_str}:{minute_str} वाजले आहेत."


def _format_date_marathi(dt: datetime) -> str:
    marathi_digits = "०१२३४५६७८९"
    day = "".join(marathi_digits[int(d)] for d in str(dt.day))
    month = "".join(marathi_digits[int(d)] for d in str(dt.month))
    year = "".join(marathi_digits[int(d)] for d in str(dt.year))

    weekdays_marathi = [
        "सोमवार", "मंगळवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"
    ]
    weekday = weekdays_marathi[dt.weekday()]

    return f"आज {weekday}, {day}/{month}/{year} आहे."


TIME = {
    "response": _format_time_marathi,
}

DATE = {
    "response": _format_date_marathi,
}

IDENTITY = {
    "response": "मी Deskbot आहे, तुमचा स्मार्ट डेस्क असिस्टंट.",
}

HELP = {
    "intro": "मी तुम्हाला यथे मदत करू शकतो:",
    "get_time": "वेळ विचारा",
    "get_date": "आजची तारीख विचारा",
    "assistant_identity": "माझी ओळख विचारा",
    "assistant_help": "मदत/क्षमता दाखवा",
}

UNKNOWN = {
    "response": "माफ करा, मी अजून हे करू शकत नाही. तुम्ही मला वेळ, तारीख किंवा माझ्याबद्दल विचारू शकता.",
}

INTENT_DESCRIPTIONS = {
    "get_time": "वेळ विचारा",
    "get_date": "आजची तारीख विचारा",
    "assistant_identity": "माझी ओळख विचारा",
    "assistant_help": "मदत/क्षमता दाखवा",
}