from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class InstrumentOut(BaseModel):
    id: int
    symbol: str
    base: str
    quote: str
    product_id: str
    status: str
    min_size: float
    size_increment: float
    price_increment: float
    updated_at: datetime

    model_config = {"from_attributes": True}


class UniverseOut(BaseModel):
    symbols: list[str]
    ranked: list[dict]
    source: str
    updated_at: datetime | None = None
