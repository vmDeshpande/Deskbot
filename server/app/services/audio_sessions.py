"""Single-process audio session registry for the V1 single-worker backend."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from fastapi import WebSocket


@dataclass
class AudioSession:
    device_id: str
    recording_id: str
    websocket: WebSocket
    sample_rate: int
    channels: int
    format: str
    started_at: float = field(default_factory=time.monotonic)
    total_chunks: int = 0
    total_bytes: int = 0
    finished: bool = False


class AudioSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, AudioSession] = {}
        self._lock = asyncio.Lock()

    async def start_recording(
        self,
        device_id: str,
        recording_id: str,
        websocket: WebSocket,
        sample_rate: int,
        channels: int,
        format: str,
    ) -> AudioSession | None:
        async with self._lock:
            existing = self._sessions.get(device_id)
            if existing is not None and not existing.finished:
                return None
            if existing is not None:
                # Previous recording finished, safe to replace
                del self._sessions[device_id]

        session = AudioSession(
            device_id=device_id,
            recording_id=recording_id,
            websocket=websocket,
            sample_rate=sample_rate,
            channels=channels,
            format=format,
        )
        async with self._lock:
            self._sessions[device_id] = session
        return session

    async def get(self, device_id: str) -> AudioSession | None:
        async with self._lock:
            return self._sessions.get(device_id)

    async def is_current(self, session: AudioSession) -> bool:
        async with self._lock:
            return self._sessions.get(session.device_id) is session

    async def finish_recording(self, session: AudioSession, recording_id: str) -> bool:
        async with self._lock:
            current = self._sessions.get(session.device_id)
            if current is not session:
                return False
            if current.recording_id != recording_id:
                return False
            if current.finished:
                return False
            current.finished = True
            return True

    async def remove(self, session: AudioSession) -> bool:
        async with self._lock:
            if self._sessions.get(session.device_id) is not session:
                return False
            del self._sessions[session.device_id]
            return True

    async def shutdown(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            try:
                await session.websocket.close(code=1001, reason="server shutdown")
            except Exception:
                pass