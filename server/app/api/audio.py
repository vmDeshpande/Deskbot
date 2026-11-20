"""Authenticated Deskbot audio WebSocket endpoint."""

from __future__ import annotations

import asyncio
import logging
import time
from uuid import UUID, uuid4

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.api.audio_schemas import (
    AudioCompletedMessage,
    AudioErrorMessage,
    AudioFinishMessage,
    AudioProtocolError,
    AudioStartMessage,
    AudioStartedMessage,
    ServerAudioErrorMessage,
    parse_audio_message,
    utc_timestamp,
)
from app.security.device_auth import parse_bearer_header, verify_device_token
from app.services.audio_sessions import AudioSession, AudioSessionManager

logger = logging.getLogger(__name__)
AUDIO_SUBPROTOCOL = "deskbot.audio.v1"


def _envelope(message_type: str, payload: dict) -> dict:
    return {
        "protocol_version": "1.0",
        "message_id": str(uuid4()),
        "message_type": message_type,
        "timestamp": utc_timestamp(),
        "payload": payload,
    }


async def _send_json(websocket: WebSocket, message: dict, settings) -> bool:
    try:
        await asyncio.wait_for(
            websocket.send_json(message), timeout=settings.websocket_send_timeout_s
        )
        logger.info(
            "event=audio.response_sent device_id=%s message_type=%s",
            message.get("payload", {}).get("device_id", "unknown"),
            message.get("message_type", "unknown"),
        )
        return True
    except (asyncio.TimeoutError, RuntimeError, OSError):
        logger.warning("Failed to send audio response")
        return False


async def _close(websocket: WebSocket, code: int, reason: str) -> None:
    try:
        await websocket.close(code=code, reason=reason[:120])
    except (RuntimeError, OSError):
        pass


async def audio_socket(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    manager: AudioSessionManager = websocket.app.state.audio_sessions
    offered = websocket.scope.get("subprotocols", [])
    if AUDIO_SUBPROTOCOL not in offered:
        await _close(websocket, 1008, "required subprotocol deskbot.audio.v1")
        return
    token = parse_bearer_header(websocket.headers.get("authorization"))
    authenticated_device_id = verify_device_token(token, settings.device_token_hashes)
    if authenticated_device_id is None:
        await _close(websocket, 1008, "authentication failed")
        return

    await websocket.accept(subprotocol=AUDIO_SUBPROTOCOL)
    logger.info("event=audio.accepted device_id=%s", authenticated_device_id)

    session: AudioSession | None = None
    recording_started = False

    try:
        while await manager.is_current(session) if session else True:
            try:
                frame = await asyncio.wait_for(
                    websocket.receive(), timeout=settings.audio_chunk_timeout_s
                )
            except asyncio.TimeoutError:
                if session and not session.finished:
                    logger.info("event=audio.chunk_timeout device_id=%s", session.device_id)
                    await _close(websocket, 1001, "audio chunk timeout")
                break

            if frame.get("type") == "websocket.disconnect":
                logger.info(
                    "event=audio.client_disconnected device_id=%s code=%s",
                    session.device_id if session else authenticated_device_id,
                    frame.get("code", "unknown"),
                )
                break

            if frame.get("bytes") is not None:
                if not session or not recording_started or session.finished:
                    await _close(websocket, 1008, "binary frame not allowed in current state")
                    break
                chunk = frame["bytes"]
                if len(chunk) == 0:
                    await _close(websocket, 1008, "empty audio chunk")
                    break
                if len(chunk) % 2 != 0:
                    await _close(websocket, 1008, "odd-sized PCM chunk")
                    break
                if len(chunk) > settings.audio_max_chunk_bytes:
                    await _close(websocket, 1009, "audio chunk too large")
                    break

                elapsed_ms = int((time.monotonic() - session.started_at) * 1000)
                if elapsed_ms > settings.audio_max_duration_s * 1000:
                    await _close(websocket, 1008, "recording too long")
                    break

                session.total_chunks += 1
                session.total_bytes += len(chunk)
                continue

            text = frame.get("text", "")
            if not text:
                await _close(websocket, 1008, "empty message")
                break

            try:
                message = parse_audio_message(text, settings.websocket_max_message_bytes)
            except AudioProtocolError as exc:
                logger.warning(
                    "event=audio.protocol_rejected device_id=%s code=%s",
                    session.device_id if session else authenticated_device_id,
                    exc.code,
                )
                error_msg = _envelope(
                    "server.audio.error",
                    {
                        "code": exc.code,
                        "message": exc.code.replace("_", " "),
                        "related_message_id": None,
                        "retryable": not exc.fatal,
                    },
                )
                await _send_json(websocket, error_msg, settings)
                if exc.fatal:
                    await _close(websocket, exc.close_code, exc.code)
                break

            if isinstance(message, AudioStartMessage):
                if session and recording_started and not session.finished:
                    await _close(websocket, 1008, "recording already in progress")
                    break

                if (
                    message.payload.sample_rate != settings.audio_sample_rate
                    or message.payload.channels != settings.audio_channels
                    or message.payload.format != settings.audio_format
                ):
                    await _close(websocket, 1008, "invalid audio format")
                    break

                session = await manager.start_recording(
                    device_id=authenticated_device_id,
                    recording_id=str(message.payload.recording_id),
                    websocket=websocket,
                    sample_rate=message.payload.sample_rate,
                    channels=message.payload.channels,
                    format=message.payload.format,
                )
                recording_started = True
                logger.info(
                    "event=audio.started device_id=%s recording_id=%s",
                    session.device_id,
                    session.recording_id,
                )

                await _send_json(
                    websocket,
                    _envelope(
                        "audio.started",
                        {
                            "recording_id": session.recording_id,
                            "sample_rate": session.sample_rate,
                            "channels": session.channels,
                            "format": session.format,
                            "max_duration_s": settings.audio_max_duration_s,
                            "max_chunk_bytes": settings.audio_max_chunk_bytes,
                        },
                    ),
                    settings,
                )
                continue

            if not session:
                await _close(websocket, 1008, "recording not started")
                break

            if str(message.payload.recording_id) != session.recording_id:
                await _close(websocket, 1008, "recording ID mismatch")
                break

            if isinstance(message, AudioFinishMessage):
                if session.finished:
                    await _close(websocket, 1008, "recording already finished")
                    break

                finished_ok = await manager.finish_recording(session, str(message.payload.recording_id))
                if not finished_ok:
                    await _close(websocket, 1008, "recording ID mismatch or already finished")
                    break

                duration_ms = int((time.monotonic() - session.started_at) * 1000)

                logger.info(
                    "event=audio.completed device_id=%s recording_id=%s chunks=%d bytes=%d duration_ms=%d",
                    session.device_id,
                    session.recording_id,
                    session.total_chunks,
                    session.total_bytes,
                    duration_ms,
                )

                await _send_json(
                    websocket,
                    _envelope(
                        "audio.completed",
                        {
                            "recording_id": session.recording_id,
                            "total_chunks": session.total_chunks,
                            "total_bytes": session.total_bytes,
                            "duration_ms": duration_ms,
                        },
                    ),
                    settings,
                )
                continue

            await _close(websocket, 1008, "message direction is invalid")
            break

    except WebSocketDisconnect as exc:
        logger.info(
            "event=audio.client_disconnected device_id=%s code=%s",
            session.device_id if session else authenticated_device_id,
            exc.code,
        )
    except asyncio.CancelledError:
        raise
    except (asyncio.TimeoutError, OSError, RuntimeError) as exc:
        logger.info(
            "event=audio.connection_ended device_id=%s error_type=%s",
            session.device_id if session else authenticated_device_id,
            type(exc).__name__,
        )
    finally:
        if session is not None:
            logger.info("event=audio.session_cleanup_started device_id=%s", session.device_id)
            await manager.remove(session)
            logger.info("event=audio.session_cleanup device_id=%s", session.device_id)