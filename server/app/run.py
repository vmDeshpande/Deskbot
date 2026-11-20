"""Configured Uvicorn entry point for Windows and single-worker deployment."""

import uvicorn

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        workers=1,
        ws="websockets-sansio",
        ws_max_size=settings.websocket_max_message_bytes,
        ws_ping_interval=20.0,
        ws_ping_timeout=20.0,
        ws_per_message_deflate=False,
    )


if __name__ == "__main__":
    main()
