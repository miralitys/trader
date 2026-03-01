from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class Message(BaseModel):
    message: str


class PaginatedResponse(BaseModel):
    total: int


class Timestamped(BaseModel):
    created_at: datetime

    model_config = {"from_attributes": True}
