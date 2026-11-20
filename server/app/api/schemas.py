"""Pydantic response models for the versioned API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class RootResponse(BaseModel):
    message: str
    service: str
    version: str


class HealthResponse(BaseModel):
    status: Literal["ok"]
    timestamp: datetime


class StatusResponse(BaseModel):
    service: str
    version: str
    environment: str
    status: Literal["running"]
