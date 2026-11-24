"""DateTime command handlers."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.commands.localization import format_time, format_date, get_unknown_response
from app.services.commands.models import CommandContext, CommandIntent, CommandResult
from app.services.commands.registry import CommandHandler


class TimeCommandHandler(CommandHandler):
    """Handler for current time queries."""

    @property
    def intent_name(self) -> str:
        return "get_time"

    def _get_timezone(self, tz_name: str) -> ZoneInfo:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            return ZoneInfo("Asia/Kolkata")

    async def execute(self, context: CommandContext) -> CommandResult:
        tz = self._get_timezone(context.timezone)
        now = datetime.now(tz)
        response_text = format_time(context.detected_language, now)
        return CommandResult(
            intent=CommandIntent(name=self.intent_name),
            recognized=True,
            response_text=response_text,
            response_language=context.detected_language,
            metadata={"timezone": context.timezone, "iso_time": now.isoformat()},
        )


class DateCommandHandler(CommandHandler):
    """Handler for current date queries."""

    @property
    def intent_name(self) -> str:
        return "get_date"

    def _get_timezone(self, tz_name: str) -> ZoneInfo:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            return ZoneInfo("Asia/Kolkata")

    async def execute(self, context: CommandContext) -> CommandResult:
        tz = self._get_timezone(context.timezone)
        now = datetime.now(tz)
        response_text = format_date(context.detected_language, now)
        return CommandResult(
            intent=CommandIntent(name=self.intent_name),
            recognized=True,
            response_text=response_text,
            response_language=context.detected_language,
            metadata={"timezone": context.timezone, "iso_date": now.date().isoformat()},
        )