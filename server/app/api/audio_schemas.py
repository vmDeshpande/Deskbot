"""Validated Deskbot Audio WebSocket V1 messages."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
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

AudioSampleRate = Annotated[StrictInt, Field(ge=8000, le=48000)]
AudioChannels = Annotated[StrictInt, Field(ge=1, le=2)]
AudioFormat = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=32)]


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


class AudioStartPayload(PayloadModel):
    recording_id: UUID4
    sample_rate: AudioSampleRate
    channels: AudioChannels
    format: AudioFormat


class AudioStartMessage(MessageEnvelope):
    message_type: Literal["audio.start"]
    payload: AudioStartPayload


class AudioStartedPayload(PayloadModel):
    recording_id: UUID4
    sample_rate: AudioSampleRate
    channels: AudioChannels
    format: AudioFormat
    max_duration_s: Annotated[StrictInt, Field(ge=1, le=300)]
    max_chunk_bytes: Annotated[StrictInt, Field(ge=320, le=32000)]


class AudioStartedMessage(MessageEnvelope):
    message_type: Literal["audio.started"]
    payload: AudioStartedPayload


class AudioFinishPayload(PayloadModel):
    recording_id: UUID4
    total_chunks: NonNegativeUInt32 | None = None
    total_bytes: NonNegativeUInt32 | None = None


class AudioFinishMessage(MessageEnvelope):
    message_type: Literal["audio.finish"]
    payload: AudioFinishPayload


class AudioCompletedPayload(PayloadModel):
    recording_id: UUID4
    total_chunks: NonNegativeUInt32
    total_bytes: NonNegativeUInt32
    duration_ms: NonNegativeUInt32


class AudioCompletedMessage(MessageEnvelope):
    message_type: Literal["audio.completed"]
    payload: AudioCompletedPayload


AudioErrorCode = Literal[
    "invalid_format",
    "invalid_recording_state",
    "recording_too_long",
    "chunk_too_large",
    "invalid_chunk",
    "recording_id_mismatch",
    "internal_error",
]


class AudioErrorPayload(PayloadModel):
    code: AudioErrorCode
    message: MessageText
    related_message_id: UUID4 | None = None
    retryable: StrictBool


class AudioErrorMessage(MessageEnvelope):
    message_type: Literal["audio.error"]
    payload: AudioErrorPayload


class ServerAudioErrorMessage(MessageEnvelope):
    message_type: Literal["server.audio.error"]
    payload: AudioErrorPayload


AudioMessage: TypeAlias = (
    AudioStartMessage
    | AudioStartedMessage
    | AudioFinishMessage
    | AudioCompletedMessage
    | AudioErrorMessage
    | ServerAudioErrorMessage
)

AUDIO_MESSAGE_MODELS: dict[str, type[MessageEnvelope]] = {
    "audio.start": AudioStartMessage,
    "audio.started": AudioStartedMessage,
    "audio.finish": AudioFinishMessage,
    "audio.completed": AudioCompletedMessage,
    "audio.error": AudioErrorMessage,
    "server.audio.error": ServerAudioErrorMessage,
}
AUDIO_ENVELOPE_KEYS = {"protocol_version", "message_id", "message_type", "timestamp", "payload"}


class AudioProtocolError(ValueError):
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


def parse_audio_message(data: str | bytes, max_bytes: int = MAX_MESSAGE_BYTES) -> AudioMessage:
    """Parse a complete text message with byte, JSON, envelope, and payload checks."""

    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise AudioProtocolError("invalid_utf8", 1007) from exc
    elif isinstance(data, str):
        text = data
    else:
        raise AudioProtocolError("invalid_message_data", 1007)

    try:
        encoded = text.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise AudioProtocolError("invalid_utf8", 1007) from exc
    if len(encoded) > max_bytes:
        raise AudioProtocolError("message_too_large", 1009)

    try:
        raw = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
        _ensure_finite_numbers(raw)
    except _DuplicateJSONKey as exc:
        raise AudioProtocolError("duplicate_json_key", 1007) from exc
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise AudioProtocolError("invalid_json", 1007) from exc

    if not isinstance(raw, dict):
        raise AudioProtocolError("invalid_envelope", 1008, fatal=False)
    if set(raw) != AUDIO_ENVELOPE_KEYS:
        raise AudioProtocolError("invalid_envelope", 1008, fatal=False)
    if raw.get("protocol_version") != PROTOCOL_VERSION:
        raise AudioProtocolError("unsupported_version", 1008)
    message_type = raw.get("message_type")
    if not isinstance(message_type, str) or message_type not in AUDIO_MESSAGE_MODELS:
        raise AudioProtocolError("unsupported_message_type", 1008)

    model = AUDIO_MESSAGE_MODELS[message_type]
    try:
        return model.model_validate(raw)  # type: ignore[return-value]
    except ValidationError as exc:
        raise AudioProtocolError("invalid_payload", 1008, fatal=False) from exc


def utc_timestamp() -> str:
    """Return an RFC 3339 UTC timestamp suitable for an envelope."""

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")