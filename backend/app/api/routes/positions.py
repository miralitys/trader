from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.entities import Instrument, Position, User
from app.schemas.position import PositionOut

router = APIRouter()


@router.get("/positions", response_model=list[PositionOut])
def list_positions(
    mode: str = Query("paper", pattern="^(paper|live)$"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[PositionOut]:
    rows = db.scalars(
        select(Position).where(Position.mode == mode).order_by(Position.updated_at.desc()).limit(500)
    ).all()
    instrument_ids = list({row.instrument_id for row in rows})
    instruments = db.scalars(select(Instrument).where(Instrument.id.in_(instrument_ids))).all() if instrument_ids else []
    lookup = {inst.id: inst.symbol for inst in instruments}

    out: list[PositionOut] = []
    for row in rows:
        item = PositionOut.model_validate(row)
        item.symbol = lookup.get(row.instrument_id)
        out.append(item)
    return out
