"""WebSocket integration tests using FastAPI/Starlette's in-process client."""

import asyncio
import json
import logging
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from fastapi import WebSocketDisconnect

from app.api.websocket_schemas import utc_timestamp
from app.main import create_app
from app.config import Settings
from app.security.device_auth import token_digest

TOKEN = "a" * 64
DEVICE = "593a0d29-c053-4c40-9c3c-8d7d26102f7a"
BOOT = "b0e4f026-663c-4739-9e88-e7a7f8b7ce16"


def make_app(**overrides):
    values = {
        "device_token_hashes": {DEVICE: token_digest(TOKEN)},
        "device_registration_timeout_s": 0.1,
        "device_heartbeat_timeout_s": 2.0,
    }
    values.update(overrides)
    return create_app(Settings(_env_file=None, **values))


def message(kind, payload, message_id=None, **overrides):
    value = {
        "protocol_version": "1.0",
        "message_id": message_id or str(uuid4()),
        "message_type": kind,
        "timestamp": utc_timestamp(),
        "payload": payload,
    }
    value.update(overrides)
    return json.dumps(value)


def register(ws, *, device_id=DEVICE, boot_id=BOOT):
    ws.send_text(message("device.register", {
        "device_id": device_id,
        "boot_id": boot_id,
        "hardware_model": "ESP32-WROOM-32",
        "firmware_version": "0.1.0",
        "uptime_ms": 0,
        "capabilities": ["display", "touch"],
    }))
    return json.loads(ws.receive_text())


def connect(client, token=TOKEN, subprotocols=("deskbot.v1",), path="/ws/device"):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.websocket_connect(path, headers=headers, subprotocols=list(subprotocols))


def test_successful_registration_heartbeat_ack_and_status_counter_independence():
    with TestClient(make_app()) as client:
        with connect(client) as ws:
            registered = register(ws)
            assert registered["message_type"] == "device.registered"
            assert registered["payload"]["device_id"] == DEVICE
            assert registered["payload"]["heartbeat_interval_s"] == 30
            assert registered["payload"]["heartbeat_timeout_s"] == 2

            heartbeat_id = str(uuid4())
            ws.send_text(message("device.heartbeat", {"boot_id": BOOT, "sequence": 35, "uptime_ms": 100}, heartbeat_id))
            ack = json.loads(ws.receive_text())
            assert ack["message_type"] == "device.heartbeat.ack"
            assert ack["payload"]["reply_to"] == heartbeat_id
            assert ack["payload"]["sequence"] == 35

            ws.send_text(message("device.status", {"boot_id": BOOT, "sequence": 1, "state": "idle"}))
            ws.send_text(message("device.status", {"boot_id": BOOT, "sequence": 1, "state": "speaking"}))
            ws.send_text(message("device.heartbeat", {"boot_id": BOOT, "sequence": 35, "uptime_ms": 101}))
            duplicate_ack = json.loads(ws.receive_text())
            assert duplicate_ack["payload"]["sequence"] == 35
            session = asyncio.run(client.app.state.device_sessions.get(DEVICE))
            assert session.last_status_sequence == 1
            assert session.last_status["state"] == "idle"
            assert session.last_heartbeat_sequence == 35


def test_lifecycle_diagnostics_cover_registration_and_heartbeat_without_credentials(caplog):
    caplog.set_level(logging.INFO, logger="app.api.websocket")
    with TestClient(make_app()) as client:
        with connect(client) as ws:
            register(ws)
            ws.send_text(message("device.heartbeat", {
                "boot_id": BOOT,
                "sequence": 1,
                "uptime_ms": 100,
            }))
            acknowledgement = json.loads(ws.receive_text())
            assert acknowledgement["message_type"] == "device.heartbeat.ack"

        with connect(client) as ws:
            ws.send_text(message("device.register", {
                "device_id": str(uuid4()),
                "boot_id": BOOT,
                "hardware_model": "ESP32-WROOM-32",
                "firmware_version": "0.1.0",
                "uptime_ms": 0,
                "capabilities": [],
            }))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()

    output = caplog.text
    for event in (
        "event=websocket.accepted",
        "event=websocket.registration_frame_received",
        "event=websocket.registration_validated",
        "event=websocket.response_queued",
        "event=websocket.response_sent",
        "event=websocket.heartbeat_received",
        "event=websocket.client_disconnected",
        "event=websocket.session_cleanup_started",
        "event=websocket.registration_rejected reason=device_identity_mismatch",
    ):
        assert event in output
    assert "message_type=device.registered" in output
    assert "message_type=device.heartbeat.ack" in output
    assert TOKEN not in output
    assert token_digest(TOKEN) not in output
    assert "Authorization" not in output


@pytest.mark.parametrize("token,protocols", [
    (None, ("deskbot.v1",)),
    ("invalid", ("deskbot.v1",)),
    (TOKEN, ("unsupported",)),
    (TOKEN, ()),
])
def test_authentication_and_subprotocol_rejections(token, protocols):
    with TestClient(make_app()) as client:
        with pytest.raises(WebSocketDisconnect):
            with connect(client, token, protocols):
                pass


def test_wrong_device_identity_and_messages_before_registration_are_rejected():
    with TestClient(make_app()) as client:
        with connect(client) as ws:
            ws.send_text(message("device.register", {
                "device_id": str(uuid4()), "boot_id": BOOT, "hardware_model": "ESP32",
                "firmware_version": "0.1.0", "uptime_ms": 0, "capabilities": [],
            }))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()

        with connect(client) as ws:
            ws.send_text(message("device.heartbeat", {"boot_id": BOOT, "sequence": 1, "uptime_ms": 0}))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()


def test_registration_timeout_binary_and_invalid_message_handling():
    with TestClient(make_app(device_registration_timeout_s=0.02)) as client:
        with connect(client) as ws:
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()

    with TestClient(make_app()) as client:
        with connect(client) as ws:
            ws.send_bytes(b"audio")
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()
        with connect(client) as ws:
            ws.send_text("{")
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()


def test_invalid_boot_and_decreasing_heartbeat_sequence_are_rejected():
    with TestClient(make_app()) as client:
        with connect(client) as ws:
            register(ws)
            ws.send_text(message("device.heartbeat", {"boot_id": str(uuid4()), "sequence": 1, "uptime_ms": 0}))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()

    with TestClient(make_app()) as client:
        with connect(client) as ws:
            register(ws)
            for seq in (2, 1):
                ws.send_text(message("device.heartbeat", {"boot_id": BOOT, "sequence": seq, "uptime_ms": 0}))
                if seq == 2:
                    ws.receive_text()
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()


def test_invalid_registration_and_status_payloads_are_rejected():
    with TestClient(make_app()) as client:
        with connect(client) as ws:
            ws.send_text(message("device.register", {"device_id": DEVICE}))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()

    with TestClient(make_app()) as client:
        with connect(client) as ws:
            register(ws)
            ws.send_text(message("device.status", {"boot_id": BOOT, "sequence": 1, "state": "launching"}))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()


def test_duplicate_device_session_replaces_old_session_safely():
    application = make_app()
    with TestClient(application) as client:
        with connect(client) as old_ws:
            old_registration = register(old_ws)
            with connect(client) as new_ws:
                new_registration = register(new_ws)
                assert new_registration["payload"]["session_id"] != old_registration["payload"]["session_id"]
                with pytest.raises(WebSocketDisconnect):
                    old_ws.receive_text()
                current = asyncio.run(client.app.state.device_sessions.get(DEVICE))
                assert current.session_id == new_registration["payload"]["session_id"]
    # Lifespan shutdown clears any session whose disconnect task hasn't finished yet.
    assert asyncio.run(application.state.device_sessions.get(DEVICE)) is None


def test_websocket_route_path_is_configurable():
    with TestClient(make_app(websocket_path="/devices/ws")) as client:
        with connect(client, path="/devices/ws") as ws:
            assert register(ws)["message_type"] == "device.registered"


def test_protocol_strikes_and_inbound_rate_limit_close_session():
    with TestClient(make_app(websocket_max_protocol_strikes=2)) as client:
        with connect(client) as ws:
            register(ws)
            malformed = message("device.heartbeat", {"boot_id": BOOT, "sequence": 0, "uptime_ms": 1})
            ws.send_text(malformed)
            ws.send_text(malformed)
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()

    with TestClient(make_app(websocket_max_messages_per_minute=1)) as client:
        with connect(client) as ws:
            register(ws)
            ws.send_text(message("device.heartbeat", {"boot_id": BOOT, "sequence": 1, "uptime_ms": 1}))
            assert json.loads(ws.receive_text())["message_type"] == "device.heartbeat.ack"
            ws.send_text(message("device.heartbeat", {"boot_id": BOOT, "sequence": 2, "uptime_ms": 2}))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()

def test_http_endpoints_remain_available_alongside_websocket_route():
    with TestClient(make_app()) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/status").status_code == 200
        assert client.get("/docs").status_code == 200
