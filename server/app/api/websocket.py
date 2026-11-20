"""Authenticated Deskbot device WebSocket endpoint."""

from __future__ import annotations

import asyncio
import logging
import time
from uuid import UUID, uuid4

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.api.websocket_schemas import (
    DeviceHeartbeatMessage,
    DeviceRegisterMessage,
    DeviceStatusMessage,
    ProtocolMessageError,
    ServerPongMessage,
    parse_message,
    utc_timestamp,
)
from app.security.device_auth import parse_bearer_header, verify_device_token
from app.services.device_sessions import (
    DeviceSession,
    DeviceSessionManager,
    heartbeat_is_stale,
    record_heartbeat,
)

logger = logging.getLogger(__name__)
SUBPROTOCOL = "deskbot.v1"


def _envelope(message_type: str, payload: dict) -> dict:
    return {
        "protocol_version": "1.0",
        "message_id": str(uuid4()),
        "message_type": message_type,
        "timestamp": utc_timestamp(),
        "payload": payload,
    }


async def _queue(session: DeviceSession, message: dict, settings) -> bool:
    try:
        await asyncio.wait_for(session.outbound.put(message), timeout=settings.websocket_send_timeout_s)
        logger.info(
            "event=websocket.response_queued device_id=%s message_type=%s",
            session.device_id,
            message.get("message_type", "unknown"),
        )
        return True
    except (asyncio.TimeoutError, asyncio.QueueFull):
        logger.warning("Device outbound queue saturated; closing session device_id=%s", session.device_id)
        try:
            await session.websocket.close(code=1013, reason="outbound queue saturated")
        except (RuntimeError, OSError):
            pass
        return False


async def _sender(session: DeviceSession, settings) -> None:
    try:
        while True:
            message = await session.outbound.get()
            await asyncio.wait_for(
                session.websocket.send_json(message), timeout=settings.websocket_send_timeout_s
            )
            logger.info(
                "event=websocket.response_sent device_id=%s message_type=%s",
                session.device_id,
                message.get("message_type", "unknown"),
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "event=websocket.sender_failed device_id=%s error_type=%s",
            session.device_id,
            type(exc).__name__,
        )
        await _close(session.websocket, 1013, "outbound connection stalled")


async def _close(websocket: WebSocket, code: int, reason: str) -> None:
    try:
        await websocket.close(code=code, reason=reason[:120])
    except (RuntimeError, OSError):
        pass


async def device_socket(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    manager: DeviceSessionManager = websocket.app.state.device_sessions
    offered = websocket.scope.get("subprotocols", [])
    if SUBPROTOCOL not in offered:
        await _close(websocket, 1008, "required subprotocol deskbot.v1")
        return
    token = parse_bearer_header(websocket.headers.get("authorization"))
    authenticated_device_id = verify_device_token(token, settings.device_token_hashes)
    if authenticated_device_id is None:
        await _close(websocket, 1008, "authentication failed")
        return

    await websocket.accept(subprotocol=SUBPROTOCOL)
    logger.info("event=websocket.accepted device_id=%s", authenticated_device_id)
    session: DeviceSession | None = None
    sender: asyncio.Task | None = None
    try:
        try:
            frame = await asyncio.wait_for(
                websocket.receive(), timeout=settings.device_registration_timeout_s
            )
        except asyncio.TimeoutError:
            logger.warning("event=websocket.registration_rejected reason=registration_timeout")
            await _close(websocket, 1008, "registration timeout")
            return
        if frame.get("type") == "websocket.disconnect":
            logger.info(
                "event=websocket.client_disconnected phase=awaiting_registration code=%s",
                frame.get("code", "unknown"),
            )
            return
        if frame.get("bytes") is not None:
            logger.warning(
                "event=websocket.registration_rejected reason=binary_registration_frame"
            )
            await _close(websocket, 1003, "binary frames are unsupported in V1")
            return
        logger.info("event=websocket.registration_frame_received frame_type=text")
        try:
            message = parse_message(frame.get("text", ""), settings.websocket_max_message_bytes)
        except ProtocolMessageError as exc:
            logger.warning(
                "event=websocket.registration_rejected reason=protocol_validation code=%s",
                exc.code,
            )
            await _close(websocket, exc.close_code, exc.code)
            return
        if not isinstance(message, DeviceRegisterMessage):
            logger.warning(
                "event=websocket.registration_rejected reason=first_message_not_register"
            )
            await _close(websocket, 1008, "registration must be first")
            return
        registration = message.payload
        if str(registration.device_id) != authenticated_device_id:
            logger.warning(
                "event=websocket.registration_rejected reason=device_identity_mismatch"
            )
            await _close(websocket, 1008, "device identity mismatch")
            return

        logger.info("event=websocket.registration_validated device_id=%s", authenticated_device_id)
        session, previous = await manager.register(registration.device_id, registration.boot_id, websocket)
        if previous is not None:
            logger.info("event=websocket.session_replaced device_id=%s", session.device_id)
            await _close(previous.websocket, 4001, "replaced by a newer session")
        sender = asyncio.create_task(_sender(session, settings), name=f"deskbot-ws-send-{session.session_id}")
        await _queue(
            session,
            _envelope(
                "device.registered",
                {
                    "accepted": True,
                    "device_id": session.device_id,
                    "session_id": session.session_id,
                    "server_time": utc_timestamp(),
                    "heartbeat_interval_s": settings.device_heartbeat_interval_s,
                    "heartbeat_timeout_s": int(settings.device_heartbeat_timeout_s),
                    "max_message_bytes": settings.websocket_max_message_bytes,
                },
            ),
            settings,
        )

        protocol_strikes: list[float] = []
        while await manager.is_current(session):
            now = time.monotonic()
            if heartbeat_is_stale(session, now, settings.device_heartbeat_timeout_s):
                await _close(websocket, 1001, "heartbeat timeout")
                break
            remaining = settings.device_heartbeat_timeout_s - (now - session.last_heartbeat_at)
            try:
                frame = await asyncio.wait_for(websocket.receive(), timeout=remaining)
            except asyncio.TimeoutError:
                await _close(websocket, 1001, "heartbeat timeout")
                break
            if frame.get("type") == "websocket.disconnect":
                logger.info(
                    "event=websocket.client_disconnected device_id=%s code=%s",
                    session.device_id,
                    frame.get("code", "unknown"),
                )
                break
            if frame.get("bytes") is not None:
                await _close(websocket, 1003, "binary frames are unsupported in V1")
                break
            try:
                message = parse_message(frame.get("text", ""), settings.websocket_max_message_bytes)
            except ProtocolMessageError as exc:
                if exc.fatal:
                    await _close(websocket, exc.close_code, exc.code)
                    break
                logger.info("Rejected protocol message device_id=%s code=%s", session.device_id, exc.code)
                protocol_strikes = [stamp for stamp in protocol_strikes if time.monotonic() - stamp < 60]
                protocol_strikes.append(time.monotonic())
                if len(protocol_strikes) >= settings.websocket_max_protocol_strikes:
                    await _close(websocket, 1008, "too many invalid messages")
                    break
                continue

            # Enforce a bounded inbound message rate without retaining unbounded history.
            now = time.monotonic()
            session.inbound_times = [stamp for stamp in session.inbound_times if now - stamp < 60]
            if len(session.inbound_times) >= settings.websocket_max_messages_per_minute:
                await _close(websocket, 1008, "message rate exceeded")
                break
            session.inbound_times.append(now)

            if isinstance(message, DeviceHeartbeatMessage):
                if str(message.payload.boot_id) != session.boot_id:
                    await _close(websocket, 1008, "boot identity mismatch")
                    break
                sequence = message.payload.sequence
                try:
                    advanced = record_heartbeat(
                        session,
                        sequence,
                        now,
                        minimum_interval_s=max(1, settings.device_heartbeat_interval_s - 3),
                    )
                except ValueError as exc:
                    reason = "heartbeat sequence decreased" if "decreased" in str(exc) else "heartbeat arrived too early"
                    await _close(websocket, 1008, reason)
                    break
                logger.info(
                    "event=websocket.heartbeat_received device_id=%s sequence=%d liveness_advanced=%s",
                    session.device_id,
                    sequence,
                    str(advanced).lower(),
                )
                await _queue(
                    session,
                    _envelope(
                        "device.heartbeat.ack",
                        {
                            "reply_to": str(message.message_id),
                            "sequence": sequence,
                            "server_time": utc_timestamp(),
                        },
                    ),
                    settings,
                )
            elif isinstance(message, DeviceStatusMessage):
                if str(message.payload.boot_id) != session.boot_id:
                    await _close(websocket, 1008, "boot identity mismatch")
                    break
                if message.payload.sequence < session.last_status_sequence:
                    await _close(websocket, 1008, "status sequence decreased")
                    break
                if message.payload.sequence == session.last_status_sequence:
                    # A duplicate status is ignored; the heartbeat counter remains independent.
                    continue
                if session.last_status_at and now - session.last_status_at < 1.0:
                    await _close(websocket, 1008, "status rate exceeded")
                    break
                session.last_status_sequence = message.payload.sequence
                session.last_status_at = now
                session.last_status = message.payload.model_dump(mode="json")
            elif isinstance(message, ServerPongMessage):
                # Server ping generation is reserved for a later milestone.
                continue
            else:
                # device.error is device-to-server. Server-originated failures use close codes.
                if message.message_type == "device.error":
                    logger.info("Device reported error device_id=%s code=%s", session.device_id, message.payload.code)
                    continue
                await _close(websocket, 1008, "message direction is invalid")
                break
    except WebSocketDisconnect as exc:
        logger.info(
            "event=websocket.client_disconnected device_id=%s code=%s",
            session.device_id if session is not None else authenticated_device_id,
            exc.code,
        )
    except asyncio.CancelledError:
        raise
    except (asyncio.TimeoutError, OSError, RuntimeError) as exc:
        logger.info(
            "event=websocket.connection_ended device_id=%s error_type=%s",
            session.device_id if session is not None else authenticated_device_id,
            type(exc).__name__,
        )
    finally:
        if session is not None:
            logger.info("event=websocket.session_cleanup_started device_id=%s", session.device_id)
        if sender is not None:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
        if session is not None:
            removed = await manager.disconnect(session)
            logger.info(
                "event=websocket.session_cleanup device_id=%s removed=%s",
                session.device_id,
                str(removed).lower(),
            )
