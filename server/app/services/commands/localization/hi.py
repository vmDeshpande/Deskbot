"""Hindi localization strings."""

from datetime import datetime
from zoneinfo import ZoneInfo


def _get_timezone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def _format_time_hindi(dt: datetime) -> str:
    hour = dt.hour
    minute = dt.minute
    period = ""

    if hour == 0:
        period = "रात"
        hour_12 = 12
    elif hour < 12:
        period = "सुबह"
        hour_12 = hour
    elif hour == 12:
        period = "दोपहर"
        hour_12 = 12
    elif hour < 17:
        period = "दोपहर"
        hour_12 = hour - 12
    elif hour < 20:
        period = "शाम"
        hour_12 = hour - 12
    else:
        period = "रात"
        hour_12 = hour - 12

    hindi_digits = "०१२३४५६७८९"
    hour_str = "".join(hindi_digits[int(d)] for d in str(hour_12))
    minute_str = "".join(hindi_digits[int(d)] for d in str(minute).zfill(2))

    if minute == 0:
        return f"अभी {period} के {hour_str} बजे हैं।"
    else:
        return f"अभी {period} के {hour_str}:{minute_str} बजे हैं।"


def _format_date_hindi(dt: datetime) -> str:
    hindi_digits = "०१२३४५६७८९"
    day = "".join(hindi_digits[int(d)] for d in str(dt.day))
    month = "".join(hindi_digits[int(d)] for d in str(dt.month))
    year = "".join(hindi_digits[int(d)] for d in str(dt.year))

    weekdays_hindi = [
        "सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"
    ]
    weekday = weekdays_hindi[dt.weekday()]

    return f"आज {weekday}, {day}/{month}/{year} है।"


TIME = {
    "response": _format_time_hindi,
}

DATE = {
    "response": _format_date_hindi,
}

IDENTITY = {
    "response": "मैं Deskbot हूँ, आपका स्मार्ट डेस्क असिस्टेंट।",
}

HELP = {
    "intro": "मैं ये काम कर सकता हूँ:",
    "get_time": "समय पूछना",
    "get_date": "तारीख पूछना",
    "assistant_identity": "मेरा परिचय",
    "assistant_help": "मदद/क्षमताएं",
}

UNKNOWN = {
    "response": "माफ़ कीजिए, मैं अभी यह काम नहीं कर सकता। आप समय, तारीख या मेरे बारे में पूछ सकते हैं।",
}

INTENT_DESCRIPTIONS = {
    "get_time": "समय पूछना",
    "get_date": "तारीख पूछना",
    "assistant_identity": "मेरा परिचय",
    "assistant_help": "मदद/क्षमताएं",
}