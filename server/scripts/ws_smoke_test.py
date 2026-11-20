"""Manual registration and heartbeat smoke test using websockets' sync client."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

from websockets.sync.client import connect


def envelope(message_type: str, payload: dict) -> str:
    return json.dumps(
        {
            "protocol_version": "1.0",
            "message_id": str(uuid4()),
            "message_type": message_type,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "payload": payload,
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws/device")
    parser.add_argument("--device-id", default=os.environ.get("DESKBOT_DEVICE_ID"))
    args = parser.parse_args()
    token = os.environ.get("DESKBOT_DEVICE_TOKEN")
    if not token or not args.device_id:
        parser.error("set DESKBOT_DEVICE_TOKEN and DESKBOT_DEVICE_ID in the environment")
    device_id = UUID(args.device_id)
    boot_id = uuid4()
    with connect(
        args.url,
        additional_headers={"Authorization": f"Bearer {token}"},
        subprotocols=["deskbot.v1"],
        compression=None,
        max_size=4096,
        ping_interval=20,
        ping_timeout=20,
    ) as websocket:
        websocket.send(envelope("device.register", {
            "device_id": str(device_id),
            "boot_id": str(boot_id),
            "hardware_model": "ESP32-WROOM-32",
            "firmware_version": "development",
            "uptime_ms": 0,
            "capabilities": ["display", "touch", "mic_capture", "speaker_playback"],
        }))
        registered = json.loads(websocket.recv(timeout=5))
        print(json.dumps(registered, indent=2))
        heartbeat_id = uuid4()
        websocket.send(json.dumps({
            "protocol_version": "1.0",
            "message_id": str(heartbeat_id),
            "message_type": "device.heartbeat",
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "payload": {"boot_id": str(boot_id), "sequence": 1, "uptime_ms": 1000},
        }))
        print(json.dumps(json.loads(websocket.recv(timeout=5)), indent=2))


if __name__ == "__main__":
    main()
