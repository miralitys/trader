from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class SignalOut(BaseModel):
    id: int
    instrument_id: int
    strategy: str
    timeframe: str
    signal: str
    entry: float
    stop: float
    take: float
    confidence: float
    reason: str
    created_at: datetime
    expires_at: datetime
    status: str
    meta_json: dict
    symbol: str | None = None

    model_config = {"from_attributes": True}
