from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.entities import Candle, Instrument, User
from app.schemas.candle import CandleOut

router = APIRouter()


@router.get("/candles", response_model=list[CandleOut])
def list_candles(
    symbol: str = Query(...),
    tf: str = Query("5m"),
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[CandleOut]:
    instrument = db.scalar(select(Instrument).where(Instrument.symbol == symbol.upper()))
    if not instrument:
        raise HTTPException(status_code=404, detail="Instrument not found")

    stmt = select(Candle).where(Candle.instrument_id == instrument.id, Candle.timeframe == tf)
    if from_ts:
        stmt = stmt.where(Candle.ts >= from_ts)
    if to_ts:
        stmt = stmt.where(Candle.ts <= to_ts)
    stmt = stmt.order_by(Candle.ts.asc()).limit(5000)

    rows = db.scalars(stmt).all()
    return [CandleOut.model_validate(row) for row in rows]
