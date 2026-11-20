"""Pure protocol validation tests; no network or device dependency."""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.api.websocket_schemas import ProtocolMessageError, parse_message, utc_timestamp


def envelope(kind="device.heartbeat", payload=None, **updates):
    value = {
        "protocol_version": "1.0",
        "message_id": str(uuid4()),
        "message_type": kind,
        "timestamp": utc_timestamp(),
        "payload": payload or {"boot_id": str(uuid4()), "sequence": 1, "uptime_ms": 1},
    }
    value.update(updates)
    return value


def test_valid_heartbeat_message_parses():
    assert parse_message(json.dumps(envelope())).message_type == "device.heartbeat"


@pytest.mark.parametrize("raw", ["{", "[]", "null", "{}"])
def test_invalid_json_or_envelope_rejected(raw):
    with pytest.raises(ProtocolMessageError):
        parse_message(raw)


def test_duplicate_json_keys_rejected():
    with pytest.raises(ProtocolMessageError, match="duplicate_json_key"):
        parse_message('{"protocol_version":"1.0","protocol_version":"1.0"}')


def test_invalid_utf8_and_oversized_message_rejected():
    with pytest.raises(ProtocolMessageError, match="invalid_utf8"):
        parse_message(b"\xff")
    with pytest.raises(ProtocolMessageError, match="message_too_large"):
        parse_message(" " * 5, max_bytes=4)


@pytest.mark.parametrize("updates", [
    {"protocol_version": "2.0"},
    {"extra": True},
    {"message_type": "unknown"},
    {"timestamp": "2026-01-01T00:00:00+00:00"},
])
def test_invalid_envelope_fields_rejected(updates):
    with pytest.raises(ProtocolMessageError):
        parse_message(json.dumps(envelope(**updates)))


def test_malformed_payload_and_non_finite_values_rejected():
    with pytest.raises(ProtocolMessageError):
        parse_message(json.dumps(envelope(payload={"boot_id": str(uuid4()), "sequence": 0, "uptime_ms": 1})))
    bad = envelope(payload={"boot_id": str(uuid4()), "sequence": 1, "uptime_ms": 1})
    raw = json.dumps(bad).replace('"uptime_ms": 1', '"uptime_ms": NaN')
    with pytest.raises(ProtocolMessageError):
        parse_message(raw)


def test_registration_status_ping_command_and_error_schemas():
    device_id, boot_id = str(uuid4()), str(uuid4())
    cases = [
        ("device.register", {"device_id": device_id, "boot_id": boot_id, "hardware_model": "ESP32", "firmware_version": "1.0", "uptime_ms": 0, "capabilities": ["display"]}),
        ("device.status", {"boot_id": boot_id, "sequence": 1, "state": "idle"}),
        ("server.ping", {"nonce": str(uuid4())}),
        ("server.pong", {"reply_to": str(uuid4()), "nonce": str(uuid4())}),
        ("device.error", {"code": "busy", "message": "busy", "retryable": True}),
        ("server.command", {"command_id": str(uuid4()), "name": "display.set_screen", "arguments": {"screen": "home"}, "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(timespec="milliseconds").replace("+00:00", "Z")}),
    ]
    for kind, payload in cases:
        assert parse_message(json.dumps(envelope(kind, payload))).message_type == kind


def test_registration_rejects_invalid_uuid_and_unallowlisted_capability():
    invalid_uuid_payload = {
        "device_id": "not-a-uuid", "boot_id": str(uuid4()), "hardware_model": "ESP32",
        "firmware_version": "1.0", "uptime_ms": 0, "capabilities": [],
    }
    with pytest.raises(ProtocolMessageError):
        parse_message(json.dumps(envelope("device.register", invalid_uuid_payload)))
    invalid_capability_payload = {
        "device_id": str(uuid4()), "boot_id": str(uuid4()), "hardware_model": "ESP32",
        "firmware_version": "1.0", "uptime_ms": 0, "capabilities": ["shell"],
    }
    with pytest.raises(ProtocolMessageError):
        parse_message(json.dumps(envelope("device.register", invalid_capability_payload)))


@pytest.mark.parametrize("payload", [
    {"boot_id": str(uuid4()), "sequence": 1, "state": "unknown"},
    {"boot_id": str(uuid4()), "sequence": 1, "state": "idle", "wifi_rssi_dbm": 1},
    {"boot_id": str(uuid4()), "sequence": 1, "state": "idle", "free_heap_bytes": -1},
])
def test_invalid_status_state_and_telemetry_rejected(payload):
    with pytest.raises(ProtocolMessageError):
        parse_message(json.dumps(envelope("device.status", payload)))


@pytest.mark.parametrize("expiry", [
    (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    (datetime.now(timezone.utc) + timedelta(minutes=6)).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
])
def test_server_command_expiry_is_bounded(expiry):
    payload = {
        "command_id": str(uuid4()),
        "name": "display.set_screen",
        "arguments": {"screen": "home"},
        "expires_at": expiry,
    }
    with pytest.raises(ProtocolMessageError):
        parse_message(json.dumps(envelope("server.command", payload)))


def test_device_status_and_heartbeat_use_separate_sequence_fields():
    boot = str(uuid4())
    hb = envelope("device.heartbeat", {"boot_id": boot, "sequence": 35, "uptime_ms": 100})
    status = envelope("device.status", {"boot_id": boot, "sequence": 1, "state": "idle"})
    assert parse_message(json.dumps(hb)).payload.sequence == 35
    assert parse_message(json.dumps(status)).payload.sequence == 1
