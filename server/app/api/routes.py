"""Version 1 API routes."""

from datetime import datetime, timezone

from fastapi import APIRouter, Request

from app.api.schemas import HealthResponse, StatusResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse, summary="Health check")
async def health() -> HealthResponse:
    """Report that the API process can serve requests."""

    return HealthResponse(status="ok", timestamp=datetime.now(timezone.utc))


@router.get("/status", response_model=StatusResponse, summary="Service status")
async def status(request: Request) -> StatusResponse:
    """Report backend metadata without making claims about hardware connectivity."""

    settings = request.app.state.settings
    return StatusResponse(
        service=settings.service_name,
        version=settings.version,
        environment=settings.environment,
        status="running",
    )
