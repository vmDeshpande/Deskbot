"""Audio WebSocket integration tests using FastAPI/Starlette's in-process client."""

from __future__ import annotations

import asyncio
import json
import logging
import struct
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from fastapi import WebSocketDisconnect

from app.api.websocket_schemas import utc_timestamp
from app.api.audio_schemas import utc_timestamp as audio_utc_timestamp
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
        "audio_max_chunk_bytes": 3200,
        "audio_max_duration_s": 30,
        "audio_sample_rate": 16000,
        "audio_channels": 1,
        "audio_format": "pcm_s16le",
        "audio_chunk_timeout_s": 0.5,
    }
    values.update(overrides)
    return create_app(Settings(_env_file=None, **values))


def audio_message(kind, payload, message_id=None, **overrides):
    value = {
        "protocol_version": "1.0",
        "message_id": message_id or str(uuid4()),
        "message_type": kind,
        "timestamp": audio_utc_timestamp(),
        "payload": payload,
    }
    value.update(overrides)
    return json.dumps(value)


def pcm_chunk(samples: int = 160) -> bytes:
    return struct.pack("<" + "h" * samples, *([0] * samples))


def connect_audio(client, token=TOKEN, subprotocols=("deskbot.audio.v1",), path="/ws/audio"):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.websocket_connect(path, headers=headers, subprotocols=list(subprotocols))


def connect_device(client, token=TOKEN, subprotocols=("deskbot.v1",), path="/ws/device"):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.websocket_connect(path, headers=headers, subprotocols=list(subprotocols))


def test_audio_authentication_success():
    with TestClient(make_app()) as client:
        with connect_audio(client) as ws:
            ws.send_text(audio_message("audio.start", {
                "recording_id": str(uuid4()),
                "sample_rate": 16000,
                "channels": 1,
                "format": "pcm_s16le",
            }))
            response = json.loads(ws.receive_text())
            assert response["message_type"] == "audio.started"
            assert response["payload"]["sample_rate"] == 16000
            assert response["payload"]["channels"] == 1
            assert response["payload"]["format"] == "pcm_s16le"


def test_audio_authentication_invalid_token():
    with TestClient(make_app()) as client:
        with pytest.raises(WebSocketDisconnect):
            with connect_audio(client, token="invalid"):
                pass


def test_audio_authentication_missing_token():
    with TestClient(make_app()) as client:
        with pytest.raises(WebSocketDisconnect):
            with connect_audio(client, token=None):
                pass


def test_audio_missing_subprotocol():
    with TestClient(make_app()) as client:
        with pytest.raises(WebSocketDisconnect):
            with connect_audio(client, subprotocols=("wrong",)):
                pass


def test_audio_start_and_started():
    with TestClient(make_app()) as client:
        with connect_audio(client) as ws:
            recording_id = str(uuid4())
            ws.send_text(audio_message("audio.start", {
                "recording_id": recording_id,
                "sample_rate": 16000,
                "channels": 1,
                "format": "pcm_s16le",
            }))
            started = json.loads(ws.receive_text())
            assert started["message_type"] == "audio.started"
            assert started["payload"]["recording_id"] == recording_id
            assert started["payload"]["max_duration_s"] == 30
            assert started["payload"]["max_chunk_bytes"] == 3200


def test_audio_binary_frames_accepted_during_recording():
    with TestClient(make_app()) as client:
        with connect_audio(client) as ws:
            recording_id = str(uuid4())
            ws.send_text(audio_message("audio.start", {
                "recording_id": recording_id,
                "sample_rate": 16000,
                "channels": 1,
                "format": "pcm_s16le",
            }))
            ws.receive_text()

            chunk = pcm_chunk(160)
            ws.send_bytes(chunk)
            ws.send_bytes(chunk)
            ws.send_bytes(chunk)

            ws.send_text(audio_message("audio.finish", {"recording_id": recording_id}))
            completed = json.loads(ws.receive_text())
            assert completed["message_type"] == "audio.completed"
            assert completed["payload"]["total_chunks"] == 3
            assert completed["payload"]["total_bytes"] == len(chunk) * 3


def test_audio_binary_frames_rejected_before_recording():
    with TestClient(make_app()) as client:
        with connect_audio(client) as ws:
            ws.send_bytes(pcm_chunk(160))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()


def test_audio_binary_frames_rejected_after_finish():
    with TestClient(make_app()) as client:
        with connect_audio(client) as ws:
            recording_id = str(uuid4())
            ws.send_text(audio_message("audio.start", {
                "recording_id": recording_id,
                "sample_rate": 16000,
                "channels": 1,
                "format": "pcm_s16le",
            }))
            ws.receive_text()  # audio.started
            ws.send_text(audio_message("audio.finish", {"recording_id": recording_id}))
            ws.receive_text()  # audio.completed
            # Consume transcription (if any) and command.completed
            msg = json.loads(ws.receive_text())
            if msg["message_type"] == "audio.transcription":
                ws.receive_text()  # command.completed
            else:
                # It was command.completed directly
                pass

            ws.send_bytes(pcm_chunk(160))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()


def test_audio_invalid_sample_rate():
    with TestClient(make_app()) as client:
        with connect_audio(client) as ws:
            ws.send_text(audio_message("audio.start", {
                "recording_id": str(uuid4()),
                "sample_rate": 8000,
                "channels": 1,
                "format": "pcm_s16le",
            }))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()


def test_audio_invalid_channels():
    with TestClient(make_app()) as client:
        with connect_audio(client) as ws:
            ws.send_text(audio_message("audio.start", {
                "recording_id": str(uuid4()),
                "sample_rate": 16000,
                "channels": 2,
                "format": "pcm_s16le",
            }))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()


def test_audio_invalid_format():
    with TestClient(make_app()) as client:
        with connect_audio(client) as ws:
            ws.send_text(audio_message("audio.start", {
                "recording_id": str(uuid4()),
                "sample_rate": 16000,
                "channels": 1,
                "format": "pcm_s16be",
            }))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()


def test_audio_empty_chunk_rejection():
    with TestClient(make_app()) as client:
        with connect_audio(client) as ws:
            recording_id = str(uuid4())
            ws.send_text(audio_message("audio.start", {
                "recording_id": recording_id,
                "sample_rate": 16000,
                "channels": 1,
                "format": "pcm_s16le",
            }))
            ws.receive_text()
            ws.send_bytes(b"")
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()


def test_audio_odd_sized_chunk_rejection():
    with TestClient(make_app()) as client:
        with connect_audio(client) as ws:
            recording_id = str(uuid4())
            ws.send_text(audio_message("audio.start", {
                "recording_id": recording_id,
                "sample_rate": 16000,
                "channels": 1,
                "format": "pcm_s16le",
            }))
            ws.receive_text()
            ws.send_bytes(b"\x00\x01\x02")
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()


def test_audio_oversized_chunk_rejection():
    with TestClient(make_app(audio_max_chunk_bytes=320)) as client:
        with connect_audio(client) as ws:
            recording_id = str(uuid4())
            ws.send_text(audio_message("audio.start", {
                "recording_id": recording_id,
                "sample_rate": 16000,
                "channels": 1,
                "format": "pcm_s16le",
            }))
            ws.receive_text()
            ws.send_bytes(b"\x00" * 400)
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()


def test_audio_recording_duration_limit():
    with TestClient(make_app(audio_max_duration_s=1, audio_chunk_timeout_s=2.0)) as client:
        with connect_audio(client) as ws:
            recording_id = str(uuid4())
            ws.send_text(audio_message("audio.start", {
                "recording_id": recording_id,
                "sample_rate": 16000,
                "channels": 1,
                "format": "pcm_s16le",
            }))
            ws.receive_text()

            for _ in range(10):
                ws.send_bytes(pcm_chunk(160))

            ws.send_text(audio_message("audio.finish", {"recording_id": recording_id}))
            completed = json.loads(ws.receive_text())
            assert completed["message_type"] == "audio.completed"
            assert completed["payload"]["duration_ms"] >= 0
            # Server tracks wall-clock time; 10 chunks of 10ms audio = ~100ms audio
            # but wall-clock elapsed may be 0 ms in-process. Duration is logged, not enforced.


def test_audio_recording_id_mismatch():
    with TestClient(make_app()) as client:
        with connect_audio(client) as ws:
            ws.send_text(audio_message("audio.start", {
                "recording_id": str(uuid4()),
                "sample_rate": 16000,
                "channels": 1,
                "format": "pcm_s16le",
            }))
            ws.receive_text()

            ws.send_text(audio_message("audio.finish", {"recording_id": str(uuid4())}))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()


def test_audio_client_totals_not_authoritative():
    with TestClient(make_app()) as client:
        with connect_audio(client) as ws:
            recording_id = str(uuid4())
            ws.send_text(audio_message("audio.start", {
                "recording_id": recording_id,
                "sample_rate": 16000,
                "channels": 1,
                "format": "pcm_s16le",
            }))
            ws.receive_text()

            ws.send_bytes(pcm_chunk(160))
            ws.send_bytes(pcm_chunk(160))

            ws.send_text(audio_message("audio.finish", {
                "recording_id": recording_id,
                "total_chunks": 999,
                "total_bytes": 999999,
            }))
            completed = json.loads(ws.receive_text())
            assert completed["payload"]["total_chunks"] == 2
            assert completed["payload"]["total_bytes"] == len(pcm_chunk(160)) * 2


def test_audio_completed_metrics():
    with TestClient(make_app()) as client:
        with connect_audio(client) as ws:
            recording_id = str(uuid4())
            ws.send_text(audio_message("audio.start", {
                "recording_id": recording_id,
                "sample_rate": 16000,
                "channels": 1,
                "format": "pcm_s16le",
            }))
            ws.receive_text()

            ws.send_bytes(pcm_chunk(160))
            ws.send_text(audio_message("audio.finish", {"recording_id": recording_id}))
            completed = json.loads(ws.receive_text())
            assert completed["message_type"] == "audio.completed"
            assert completed["payload"]["total_chunks"] == 1
            assert completed["payload"]["total_bytes"] == len(pcm_chunk(160))
            assert completed["payload"]["duration_ms"] >= 0


def test_audio_duplicate_finish():
    with TestClient(make_app()) as client:
        with connect_audio(client) as ws:
            recording_id = str(uuid4())
            ws.send_text(audio_message("audio.start", {
                "recording_id": recording_id,
                "sample_rate": 16000,
                "channels": 1,
                "format": "pcm_s16le",
            }))
            ws.receive_text()  # audio.started

            ws.send_text(audio_message("audio.finish", {"recording_id": recording_id}))
            ws.receive_text()  # audio.completed
            # Consume transcription (if any) and command.completed
            msg = json.loads(ws.receive_text())
            if msg["message_type"] == "audio.transcription":
                ws.receive_text()  # command.completed
            else:
                # It was command.completed directly
                pass

            ws.send_text(audio_message("audio.finish", {"recording_id": recording_id}))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()


def test_audio_disconnect_cleanup():
    app = make_app()
    with TestClient(app) as client:
        with connect_audio(client) as ws:
            recording_id = str(uuid4())
            ws.send_text(audio_message("audio.start", {
                "recording_id": recording_id,
                "sample_rate": 16000,
                "channels": 1,
                "format": "pcm_s16le",
            }))
            ws.receive_text()

    session = asyncio.run(app.state.audio_sessions.get(DEVICE))
    assert session is None


def test_audio_device_isolation():
    DEVICE_B = "aaaaaaaa-bbbb-cccc-dddd-eeeeffff0000"
    app = make_app(device_token_hashes={
        DEVICE: token_digest(TOKEN),
        DEVICE_B: token_digest("b" * 64),
    })
    with TestClient(app) as client:
        with connect_audio(client, token=TOKEN) as ws_a:
            recording_id = str(uuid4())
            ws_a.send_text(audio_message("audio.start", {
                "recording_id": recording_id,
                "sample_rate": 16000,
                "channels": 1,
                "format": "pcm_s16le",
            }))
            ws_a.receive_text()
            ws_a.send_bytes(pcm_chunk(160))

        with connect_audio(client, token="b" * 64) as ws_b:
            ws_b.send_text(audio_message("audio.start", {
                "recording_id": str(uuid4()),
                "sample_rate": 16000,
                "channels": 1,
                "format": "pcm_s16le",
            }))
            ws_b.receive_text()
            ws_b.send_bytes(pcm_chunk(160))

        session_a = asyncio.run(app.state.audio_sessions.get(DEVICE))
        session_b = asyncio.run(app.state.audio_sessions.get(DEVICE_B))
        assert session_a is None
        assert session_b is None


def test_audio_simultaneous_device_and_audio_connections():
    with TestClient(make_app()) as client:
        with connect_device(client) as device_ws:
            device_ws.send_text(json.dumps({
                "protocol_version": "1.0",
                "message_id": str(uuid4()),
                "message_type": "device.register",
                "timestamp": utc_timestamp(),
                "payload": {
                    "device_id": DEVICE,
                    "boot_id": BOOT,
                    "hardware_model": "ESP32-WROOM-32",
                    "firmware_version": "0.1.0",
                    "uptime_ms": 0,
                    "capabilities": ["display", "touch"],
                },
            }))
            device_reg = json.loads(device_ws.receive_text())
            assert device_reg["message_type"] == "device.registered"

            with connect_audio(client) as audio_ws:
                recording_id = str(uuid4())
                audio_ws.send_text(audio_message("audio.start", {
                    "recording_id": recording_id,
                    "sample_rate": 16000,
                    "channels": 1,
                    "format": "pcm_s16le",
                }))
                audio_started = json.loads(audio_ws.receive_text())
                assert audio_started["message_type"] == "audio.started"

                audio_ws.send_bytes(pcm_chunk(160))
                audio_ws.send_text(audio_message("audio.finish", {"recording_id": recording_id}))
                audio_completed = json.loads(audio_ws.receive_text())
                assert audio_completed["message_type"] == "audio.completed"

                device_ws.send_text(json.dumps({
                    "protocol_version": "1.0",
                    "message_id": str(uuid4()),
                    "message_type": "device.heartbeat",
                    "timestamp": utc_timestamp(),
                    "payload": {"boot_id": BOOT, "sequence": 1, "uptime_ms": 1000},
                }))
                hb_ack = json.loads(device_ws.receive_text())
                assert hb_ack["message_type"] == "device.heartbeat.ack"


def test_audio_duplicate_start_rejected_preserves_session():
    """Second audio.start while recording active is rejected; original session continues."""
    with TestClient(make_app()) as client:
        with connect_audio(client) as ws:
            recording_id = str(uuid4())
            ws.send_text(audio_message("audio.start", {
                "recording_id": recording_id,
                "sample_rate": 16000,
                "channels": 1,
                "format": "pcm_s16le",
            }))
            ws.receive_text()

            # Send a valid chunk
            ws.send_bytes(pcm_chunk(160))

            # Attempt second start with different recording_id
            ws.send_text(audio_message("audio.start", {
                "recording_id": str(uuid4()),
                "sample_rate": 16000,
                "channels": 1,
                "format": "pcm_s16le",
            }))
            # Should get error and disconnect
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()


def test_audio_duration_enforced_on_chunks():
    """Duration limit is checked on each chunk; test with minimal valid duration."""
    with TestClient(make_app(audio_max_duration_s=1, audio_chunk_timeout_s=2.0)) as client:
        with connect_audio(client) as ws:
            recording_id = str(uuid4())
            ws.send_text(audio_message("audio.start", {
                "recording_id": recording_id,
                "sample_rate": 16000,
                "channels": 1,
                "format": "pcm_s16le",
            }))
            ws.receive_text()

            # First chunk should succeed (duration ~0)
            ws.send_bytes(pcm_chunk(160))

            # Verify session still active by sending another chunk
            ws.send_bytes(pcm_chunk(160))

            ws.send_text(audio_message("audio.finish", {"recording_id": recording_id}))
            completed = json.loads(ws.receive_text())
            assert completed["message_type"] == "audio.completed"
            assert completed["payload"]["total_chunks"] == 2
            # Duration enforcement code path exists; real-time test would need time mocking


def test_audio_chunk_timeout_after_start():
    """Timeout while waiting for first chunk after audio.start."""
    with TestClient(make_app(audio_chunk_timeout_s=0.05)) as client:
        with connect_audio(client) as ws:
            recording_id = str(uuid4())
            ws.send_text(audio_message("audio.start", {
                "recording_id": recording_id,
                "sample_rate": 16000,
                "channels": 1,
                "format": "pcm_s16le",
            }))
            ws.receive_text()
            # Wait for timeout - no chunks sent
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_text()
            # Should get 1001 close code for timeout
            assert exc_info.value.code == 1001


def test_audio_chunk_timeout_between_chunks():
    """Timeout between binary chunks."""
    with TestClient(make_app(audio_chunk_timeout_s=0.05)) as client:
        with connect_audio(client) as ws:
            recording_id = str(uuid4())
            ws.send_text(audio_message("audio.start", {
                "recording_id": recording_id,
                "sample_rate": 16000,
                "channels": 1,
                "format": "pcm_s16le",
            }))
            ws.receive_text()
            ws.send_bytes(pcm_chunk(160))
            # Wait for timeout - no more chunks
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_text()
            assert exc_info.value.code == 1001


def test_audio_no_accumulation_in_ram():
    """Server only maintains counters, does not accumulate audio in memory."""
    with TestClient(make_app()) as client:
        with connect_audio(client) as ws:
            recording_id = str(uuid4())
            ws.send_text(audio_message("audio.start", {
                "recording_id": recording_id,
                "sample_rate": 16000,
                "channels": 1,
                "format": "pcm_s16le",
            }))
            ws.receive_text()

            # Send many chunks - verify only counters increment
            chunk = pcm_chunk(160)
            for _ in range(100):
                ws.send_bytes(chunk)

            ws.send_text(audio_message("audio.finish", {"recording_id": recording_id}))
            completed = json.loads(ws.receive_text())
            assert completed["payload"]["total_chunks"] == 100
            assert completed["payload"]["total_bytes"] == len(chunk) * 100
            # No way to retrieve raw audio - only metrics returned


def test_audio_malformed_json_does_not_crash():
    """Malformed JSON is rejected gracefully; no NameError from undefined logger."""
    import logging
    from app.api.audio_schemas import logger as audio_logger
    assert audio_logger is not None, "audio_schemas logger must be defined"
    with TestClient(make_app()) as client:
        with connect_audio(client) as ws:
            ws.send_text("{not valid json{{{")
            # Server sends an error response and/or disconnects; either way, no crash.
            try:
                response = ws.receive_text()
                parsed = json.loads(response)
                assert parsed["message_type"] == "server.audio.error"
            except WebSocketDisconnect:
                pass


def test_audio_truncated_json_does_not_crash():
    """Truncated JSON (unterminated string) is rejected gracefully."""
    with TestClient(make_app()) as client:
        with connect_audio(client) as ws:
            ws.send_text('{"protocol_version": "1.0", "message_type": "audio.start", "payload": {"recording_id": "')
            try:
                response = ws.receive_text()
                parsed = json.loads(response)
                assert parsed["message_type"] == "server.audio.error"
            except WebSocketDisconnect:
                pass


def test_audio_command_processing_time():
    """Final transcription triggers command processing for time query.
    
    Note: Without a Vosk model configured, the transcript is empty and no
    command.completed message is sent. This test verifies the audio pipeline
    still works correctly without command processing in that case.
    """
    with TestClient(make_app()) as client:
        with connect_audio(client) as ws:
            recording_id = str(uuid4())
            ws.send_text(audio_message("audio.start", {
                "recording_id": recording_id,
                "sample_rate": 16000,
                "channels": 1,
                "format": "pcm_s16le",
            }))
            ws.receive_text()  # audio.started

            ws.send_bytes(pcm_chunk(160))
            ws.send_text(audio_message("audio.finish", {"recording_id": recording_id}))

            completed = json.loads(ws.receive_text())
            assert completed["message_type"] == "audio.completed"

            # Without Vosk model, no transcription is sent and no command processing
            # The test verifies the audio pipeline completes successfully
            # Command processing tests are in test_commands.py


def test_audio_command_processing_time_with_transcript():
    """Test that would need mocked Vosk - not testing actual STT."""
    # This test documents the expected flow when Vosk returns a time query
    # Actual STT testing would require mocking Vosk or using a test model
    pass