from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.entities import Instrument, Signal, User
from app.schemas.signal import SignalOut

router = APIRouter()


def _serialize(signal: Signal, symbol: str | None = None) -> SignalOut:
    payload = SignalOut.model_validate(signal)
    payload.symbol = symbol
    return payload


@router.get("/signals", response_model=list[SignalOut])
def list_signals(
    symbol: str | None = Query(default=None),
    strategy: str | None = Query(default=None),
    status: str | None = Query(default=None),
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[SignalOut]:
    stmt = select(Signal).order_by(Signal.created_at.desc())
    if strategy:
        stmt = stmt.where(Signal.strategy == strategy)
    if status:
        stmt = stmt.where(Signal.status == status)
    if from_ts:
        stmt = stmt.where(Signal.created_at >= from_ts)
    if to_ts:
        stmt = stmt.where(Signal.created_at <= to_ts)

    if symbol:
        instrument = db.scalar(select(Instrument).where(Instrument.symbol == symbol.upper()))
        if not instrument:
            return []
        stmt = stmt.where(Signal.instrument_id == instrument.id)

    rows = db.scalars(stmt.limit(1000)).all()
    instrument_ids = list({row.instrument_id for row in rows})
    instruments = db.scalars(select(Instrument).where(Instrument.id.in_(instrument_ids))).all() if instrument_ids else []
    lookup = {inst.id: inst.symbol for inst in instruments}

    return [_serialize(row, lookup.get(row.instrument_id)) for row in rows]


@router.get("/signals/{signal_id}", response_model=SignalOut)
def get_signal(
    signal_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> SignalOut:
    row = db.scalar(select(Signal).where(Signal.id == signal_id))
    if not row:
        raise HTTPException(status_code=404, detail="Signal not found")
    instrument = db.scalar(select(Instrument).where(Instrument.id == row.instrument_id))
    return _serialize(row, instrument.symbol if instrument else None)
