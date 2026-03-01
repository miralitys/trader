from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CandleOut(BaseModel):
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    model_config = {"from_attributes": True}
