import asyncio
from uuid import UUID

import pytest
from types import SimpleNamespace

from app.services.device_sessions import (
    DeviceSessionManager,
    heartbeat_is_stale,
    record_heartbeat,
)
from app.api.websocket import _queue, _sender


class FakeWebSocket:
    def __init__(self):
        self.closed = []

    async def close(self, **kwargs):
        self.closed.append(kwargs)

    async def send_json(self, message):
        await asyncio.Event().wait()


def test_replacement_and_stale_disconnect_are_generation_safe():
    async def run():
        manager = DeviceSessionManager(queue_size=1)
        device = "593a0d29-c053-4c40-9c3c-8d7d26102f7a"
        boot = "b0e4f026-663c-4739-9e88-e7a7f8b7ce16"
        old, replaced = await manager.register(UUID(device), UUID(boot), FakeWebSocket())
        new, previous = await manager.register(UUID(device), UUID(boot), FakeWebSocket())
        assert replaced is None
        assert previous is old
        assert not await manager.disconnect(old)
        assert await manager.get(device) is new
        assert await manager.disconnect(new)
        assert await manager.get(device) is None

    asyncio.run(run())


def test_outbound_queue_is_bounded_and_shutdown_clears_state():
    async def run():
        manager = DeviceSessionManager(queue_size=1)
        ws = FakeWebSocket()
        session, _ = await manager.register(
            UUID("593a0d29-c053-4c40-9c3c-8d7d26102f7a"),
            UUID("b0e4f026-663c-4739-9e88-e7a7f8b7ce16"), ws,
        )
        session.outbound.put_nowait({"one": 1})
        try:
            session.outbound.put_nowait({"two": 2})
        except asyncio.QueueFull:
            pass
        else:
            raise AssertionError("queue accepted more than its configured capacity")
        await manager.shutdown()
        assert await manager.snapshot() == ()
        assert ws.closed[0]["code"] == 1001

    asyncio.run(run())


def test_duplicate_heartbeat_does_not_refresh_liveness_timestamp():
    async def run():
        manager = DeviceSessionManager()
        session, _ = await manager.register(
            UUID("593a0d29-c053-4c40-9c3c-8d7d26102f7a"),
            UUID("b0e4f026-663c-4739-9e88-e7a7f8b7ce16"), FakeWebSocket(),
        )
        assert record_heartbeat(session, 1, 100.0)
        assert session.last_heartbeat_at == 100.0
        assert not record_heartbeat(session, 1, 150.0)
        assert session.last_heartbeat_at == 100.0
        assert not heartbeat_is_stale(session, 189.9, 90.0)
        assert heartbeat_is_stale(session, 190.0, 90.0)
        with pytest.raises(ValueError):
            record_heartbeat(session, 0, 160.0)
        with pytest.raises(ValueError, match="too early"):
            record_heartbeat(session, 2, 126.0, minimum_interval_s=27.0)
        assert record_heartbeat(session, 2, 127.0, minimum_interval_s=27.0)

    asyncio.run(run())


def test_slow_writer_is_closed_and_full_outbound_queue_is_bounded():
    async def run():
        manager = DeviceSessionManager(queue_size=1)
        ws = FakeWebSocket()
        session, _ = await manager.register(
            UUID("593a0d29-c053-4c40-9c3c-8d7d26102f7a"),
            UUID("b0e4f026-663c-4739-9e88-e7a7f8b7ce16"), ws,
        )
        settings = SimpleNamespace(websocket_send_timeout_s=0.001)
        sender = asyncio.create_task(_sender(session, settings))
        session.outbound.put_nowait({"one": 1})
        await sender
        assert ws.closed[-1]["code"] == 1013

        ws = FakeWebSocket()
        session.websocket = ws
        session.outbound.put_nowait({"one": 1})
        assert not await _queue(session, {"two": 2}, settings)
        assert ws.closed[-1]["code"] == 1013

    asyncio.run(run())
