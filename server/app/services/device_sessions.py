"""Single-process device session registry for the V1 single-worker backend."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from fastapi import WebSocket


@dataclass
class DeviceSession:
    device_id: str
    boot_id: str
    session_id: str
    websocket: WebSocket
    outbound: asyncio.Queue[dict[str, Any]]
    last_heartbeat_sequence: int = 0
    last_status_sequence: int = 0
    last_heartbeat_at: float = field(default_factory=time.monotonic)
    last_status: dict[str, object] | None = None
    inbound_times: list[float] = field(default_factory=list)
    last_status_at: float = 0.0


class DeviceSessionManager:
    def __init__(self, queue_size: int = 32) -> None:
        self._sessions: dict[str, DeviceSession] = {}
        self._lock = asyncio.Lock()
        self.queue_size = queue_size

    async def register(self, device_id: UUID, boot_id: UUID, websocket: WebSocket) -> tuple[DeviceSession, DeviceSession | None]:
        session = DeviceSession(
            device_id=str(device_id),
            boot_id=str(boot_id),
            session_id=str(uuid4()),
            websocket=websocket,
            outbound=asyncio.Queue(maxsize=self.queue_size),
        )
        async with self._lock:
            previous = self._sessions.get(session.device_id)
            self._sessions[session.device_id] = session
        return session, previous

    async def get(self, device_id: str) -> DeviceSession | None:
        async with self._lock:
            return self._sessions.get(device_id)

    async def is_current(self, session: DeviceSession) -> bool:
        async with self._lock:
            return self._sessions.get(session.device_id) is session

    async def disconnect(self, session: DeviceSession) -> bool:
        """Remove a session only if it still owns the device registry entry."""

        async with self._lock:
            if self._sessions.get(session.device_id) is not session:
                return False
            del self._sessions[session.device_id]
            return True

    async def snapshot(self) -> tuple[DeviceSession, ...]:
        async with self._lock:
            return tuple(self._sessions.values())

    async def shutdown(self) -> None:
        sessions = await self.snapshot()
        async with self._lock:
            self._sessions.clear()
        for session in sessions:
            try:
                await session.websocket.close(code=1001, reason="server shutdown")
            except Exception:
                # A test client or peer may already have torn down the ASGI send channel.
                pass


def record_heartbeat(
    session: DeviceSession,
    sequence: int,
    received_at: float,
    minimum_interval_s: float = 0.0,
) -> bool:
    """Update liveness only for a strictly newer sequence; return whether it advanced."""

    if sequence < session.last_heartbeat_sequence:
        raise ValueError("heartbeat sequence decreased")
    if sequence == session.last_heartbeat_sequence:
        return False
    if (
        session.last_heartbeat_sequence > 0
        and received_at - session.last_heartbeat_at < minimum_interval_s
    ):
        raise ValueError("heartbeat arrived too early")
    session.last_heartbeat_sequence = sequence
    session.last_heartbeat_at = received_at
    return True


def heartbeat_is_stale(session: DeviceSession, now: float, timeout_s: float) -> bool:
    return now - session.last_heartbeat_at >= timeout_s
