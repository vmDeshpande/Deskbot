"""Validated Deskbot WebSocket V1 messages and bounded JSON parsing."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal, TypeAlias
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    UUID4,
    ValidationError,
    field_validator,
    model_validator,
)

PROTOCOL_VERSION = "1.0"
MAX_MESSAGE_BYTES = 4096
MAX_UINT32 = 2**32 - 1

_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$"
)


def _validate_utc_timestamp(value: str) -> str:
    if not _TIMESTAMP_PATTERN.fullmatch(value):
        raise ValueError("timestamp must be an RFC 3339 UTC value ending in Z")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("timestamp is not a valid RFC 3339 date-time") from exc
    return value


UtcTimestamp = Annotated[
    str,
    StringConstraints(strict=True, min_length=20, max_length=30),
    AfterValidator(_validate_utc_timestamp),
]
ShortText = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=64)]
MessageText = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=256)]
PositiveUInt32 = Annotated[StrictInt, Field(ge=1, le=MAX_UINT32)]
NonNegativeUInt32 = Annotated[StrictInt, Field(ge=0, le=MAX_UINT32)]

Capability = Literal[
    "display",
    "touch",
    "mic_capture",
    "speaker_playback",
]
DeviceState = Literal["idle", "listening", "processing", "speaking", "error"]
CommandState = Literal["accepted", "completed", "rejected", "failed"]
CommandName = Literal["display.show_message", "display.set_screen"]
DeviceErrorCode = Literal[
    "invalid_json",
    "invalid_payload",
    "unsupported_message_type",
    "unsupported_version",
    "unsupported_command",
    "command_expired",
    "busy",
    "internal_error",
]


class PayloadModel(BaseModel):
    """Payloads allow additive fields but validate all known fields strictly."""

    model_config = ConfigDict(extra="ignore")


class MessageEnvelope(BaseModel):
    """Common strict application-message envelope."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal["1.0"]
    message_id: UUID4
    message_type: str
    timestamp: UtcTimestamp
    payload: dict[str, Any]


class DeviceRegisterPayload(PayloadModel):
    device_id: UUID4
    boot_id: UUID4
    hardware_model: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=64)]
    firmware_version: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=32)]
    uptime_ms: NonNegativeUInt32
    capabilities: list[Capability] = Field(max_length=16)

    @field_validator("capabilities")
    @classmethod
    def unique_capabilities(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("capabilities must be unique")
        return value


class DeviceRegisterMessage(MessageEnvelope):
    message_type: Literal["device.register"]
    payload: DeviceRegisterPayload


class DeviceRegisteredPayload(PayloadModel):
    accepted: Literal[True]
    device_id: UUID4
    session_id: UUID4
    server_time: UtcTimestamp
    heartbeat_interval_s: Annotated[StrictInt, Field(ge=1, le=300)]
    heartbeat_timeout_s: Annotated[StrictInt, Field(ge=2, le=900)]
    max_message_bytes: Annotated[StrictInt, Field(ge=256, le=MAX_MESSAGE_BYTES)]


class DeviceRegisteredMessage(MessageEnvelope):
    message_type: Literal["device.registered"]
    payload: DeviceRegisteredPayload


class DeviceHeartbeatPayload(PayloadModel):
    boot_id: UUID4
    sequence: PositiveUInt32
    uptime_ms: NonNegativeUInt32


class DeviceHeartbeatMessage(MessageEnvelope):
    message_type: Literal["device.heartbeat"]
    payload: DeviceHeartbeatPayload


class DeviceHeartbeatAckPayload(PayloadModel):
    reply_to: UUID4
    sequence: PositiveUInt32
    server_time: UtcTimestamp


class DeviceHeartbeatAckMessage(MessageEnvelope):
    message_type: Literal["device.heartbeat.ack"]
    payload: DeviceHeartbeatAckPayload


class CommandResult(PayloadModel):
    command_id: UUID4
    state: CommandState


class DeviceStatusPayload(PayloadModel):
    boot_id: UUID4
    sequence: PositiveUInt32
    state: DeviceState
    wifi_rssi_dbm: Annotated[StrictInt, Field(ge=-127, le=0)] | None = None
    free_heap_bytes: NonNegativeUInt32 | None = None
    last_command: CommandResult | None = None


class DeviceStatusMessage(MessageEnvelope):
    message_type: Literal["device.status"]
    payload: DeviceStatusPayload


class ServerPingPayload(PayloadModel):
    nonce: UUID4


class ServerPingMessage(MessageEnvelope):
    message_type: Literal["server.ping"]
    payload: ServerPingPayload


class ServerPongPayload(PayloadModel):
    reply_to: UUID4
    nonce: UUID4


class ServerPongMessage(MessageEnvelope):
    message_type: Literal["server.pong"]
    payload: ServerPongPayload


class ShowMessageArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128)]
    duration_ms: Annotated[StrictInt, Field(ge=1, le=600_000)]


class SetScreenArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    screen: Literal["home", "status", "settings"]


CommandArguments: TypeAlias = ShowMessageArguments | SetScreenArguments


class ServerCommandPayload(PayloadModel):
    command_id: UUID4
    name: CommandName
    arguments: CommandArguments
    expires_at: UtcTimestamp

    @model_validator(mode="after")
    def arguments_match_name(self) -> ServerCommandPayload:
        expected = {
            "display.show_message": ShowMessageArguments,
            "display.set_screen": SetScreenArguments,
        }[self.name]
        if not isinstance(self.arguments, expected):
            raise ValueError("arguments do not match command name")
        expires_at = datetime.fromisoformat(self.expires_at[:-1] + "+00:00")
        now = datetime.now(timezone.utc)
        if expires_at <= now or expires_at > now + timedelta(minutes=5):
            raise ValueError("command expiry must be in the next five minutes")
        return self


class ServerCommandMessage(MessageEnvelope):
    message_type: Literal["server.command"]
    payload: ServerCommandPayload


class DeviceErrorPayload(PayloadModel):
    code: DeviceErrorCode
    message: MessageText
    related_message_id: UUID4 | None = None
    retryable: StrictBool


class DeviceErrorMessage(MessageEnvelope):
    message_type: Literal["device.error"]
    payload: DeviceErrorPayload


Message: TypeAlias = (
    DeviceRegisterMessage
    | DeviceRegisteredMessage
    | DeviceHeartbeatMessage
    | DeviceHeartbeatAckMessage
    | DeviceStatusMessage
    | ServerPingMessage
    | ServerPongMessage
    | ServerCommandMessage
    | DeviceErrorMessage
)

MESSAGE_MODELS: dict[str, type[MessageEnvelope]] = {
    "device.register": DeviceRegisterMessage,
    "device.registered": DeviceRegisteredMessage,
    "device.heartbeat": DeviceHeartbeatMessage,
    "device.heartbeat.ack": DeviceHeartbeatAckMessage,
    "device.status": DeviceStatusMessage,
    "server.ping": ServerPingMessage,
    "server.pong": ServerPongMessage,
    "server.command": ServerCommandMessage,
    "device.error": DeviceErrorMessage,
}
ENVELOPE_KEYS = {"protocol_version", "message_id", "message_type", "timestamp", "payload"}


class ProtocolMessageError(ValueError):
    """A safe-to-report protocol rejection, without embedding input contents."""

    def __init__(self, code: str, close_code: int, fatal: bool = True) -> None:
        super().__init__(code)
        self.code = code
        self.close_code = close_code
        self.fatal = fatal


class _DuplicateJSONKey(ValueError):
    pass


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKey
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    raise ValueError("non-finite JSON number")


def _ensure_finite_numbers(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    if isinstance(value, dict):
        for nested in value.values():
            _ensure_finite_numbers(nested)
    elif isinstance(value, list):
        for nested in value:
            _ensure_finite_numbers(nested)


def parse_message(data: str | bytes, max_bytes: int = MAX_MESSAGE_BYTES) -> Message:
    """Parse a complete text message with byte, JSON, envelope, and payload checks."""

    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ProtocolMessageError("invalid_utf8", 1007) from exc
    elif isinstance(data, str):
        text = data
    else:
        raise ProtocolMessageError("invalid_message_data", 1007)

    try:
        encoded = text.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ProtocolMessageError("invalid_utf8", 1007) from exc
    if len(encoded) > max_bytes:
        raise ProtocolMessageError("message_too_large", 1009)

    try:
        raw = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
        _ensure_finite_numbers(raw)
    except _DuplicateJSONKey as exc:
        raise ProtocolMessageError("duplicate_json_key", 1007) from exc
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ProtocolMessageError("invalid_json", 1007) from exc

    if not isinstance(raw, dict):
        raise ProtocolMessageError("invalid_envelope", 1008, fatal=False)
    if set(raw) != ENVELOPE_KEYS:
        raise ProtocolMessageError("invalid_envelope", 1008, fatal=False)
    if raw.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolMessageError("unsupported_version", 1008)
    message_type = raw.get("message_type")
    if not isinstance(message_type, str) or message_type not in MESSAGE_MODELS:
        raise ProtocolMessageError("unsupported_message_type", 1008)

    model = MESSAGE_MODELS[message_type]
    try:
        return model.model_validate(raw)  # type: ignore[return-value]
    except ValidationError as exc:
        raise ProtocolMessageError("invalid_payload", 1008, fatal=False) from exc


def utc_timestamp() -> str:
    """Return an RFC 3339 UTC timestamp suitable for an envelope."""

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
