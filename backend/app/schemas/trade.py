from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TradeOut(BaseModel):
    id: int
    mode: str
    instrument_id: int
    side: str
    qty_base: float
    qty_quote: float
    entry_price: float
    exit_price: float | None
    fees: float
    pnl: float
    opened_at: datetime
    closed_at: datetime | None
    status: str
    order_ids_json: dict
    meta_json: dict
    symbol: str | None = None

    model_config = {"from_attributes": True}
