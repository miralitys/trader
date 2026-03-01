from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class PositionOut(BaseModel):
    id: int
    mode: str
    instrument_id: int
    side: str
    qty_base: float
    avg_price: float
    unrealized_pnl: float
    realized_pnl: float
    opened_at: datetime
    updated_at: datetime
    status: str
    symbol: str | None = None

    model_config = {"from_attributes": True}
