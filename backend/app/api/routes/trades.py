from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.entities import Instrument, Trade, User
from app.schemas.trade import TradeOut

router = APIRouter()


@router.get("/trades", response_model=list[TradeOut])
def list_trades(
    mode: str = Query("paper", pattern="^(paper|live)$"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[TradeOut]:
    rows = db.scalars(
        select(Trade).where(Trade.mode == mode).order_by(Trade.opened_at.desc()).limit(1000)
    ).all()
    instrument_ids = list({row.instrument_id for row in rows})
    instruments = db.scalars(select(Instrument).where(Instrument.id.in_(instrument_ids))).all() if instrument_ids else []
    lookup = {inst.id: inst.symbol for inst in instruments}

    out: list[TradeOut] = []
    for row in rows:
        item = TradeOut.model_validate(row)
        item.symbol = lookup.get(row.instrument_id)
        out.append(item)
    return out
