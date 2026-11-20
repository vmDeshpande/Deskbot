"""FastAPI application entry point for Deskbot."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.api.routes import router as v1_router
from app.api.schemas import RootResponse
from app.api.websocket import device_socket
from app.config import Settings, get_settings
from app.services.device_sessions import DeviceSessionManager

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create a Deskbot API instance with explicit or environment settings."""

    app_settings = settings or get_settings()
    session_manager = DeviceSessionManager(app_settings.websocket_outbound_queue_size)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("Starting Deskbot API")
        yield
        await session_manager.shutdown()
        logger.info("Stopping Deskbot API")

    application = FastAPI(
        title=app_settings.service_name,
        version=app_settings.version,
        description="Local-first API foundation for the Deskbot smart desk assistant.",
        lifespan=lifespan,
    )
    application.state.settings = app_settings
    application.state.device_sessions = session_manager
    application.include_router(v1_router, prefix="/api/v1", tags=["v1"])
    application.add_api_websocket_route(
        app_settings.websocket_path, device_socket, name="device_socket"
    )

    @application.get("/", response_model=RootResponse, tags=["root"])
    async def root() -> RootResponse:
        """Return basic API metadata."""

        return RootResponse(
            message="Welcome to the Deskbot API",
            service=app_settings.service_name,
            version=app_settings.version,
        )

    return application


app = create_app()
