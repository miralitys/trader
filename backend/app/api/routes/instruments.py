from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.entities import Instrument, Setting, User
from app.schemas.instrument import InstrumentOut, UniverseOut

router = APIRouter()


@router.get("/instruments", response_model=list[InstrumentOut])
def list_instruments(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[InstrumentOut]:
    rows = db.scalars(select(Instrument).order_by(Instrument.symbol.asc())).all()
    return [InstrumentOut.model_validate(row) for row in rows]


@router.get("/universe/current", response_model=UniverseOut)
def current_universe(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> UniverseOut:
    setting = db.scalar(select(Setting).order_by(Setting.id.asc()).limit(1))
    if not setting:
        return UniverseOut(symbols=[], ranked=[], source="settings")

    ranked = setting.universe_json.get("ranked", [])
    last_ts = setting.universe_json.get("last_recomputed_at")
    updated_at = datetime.fromisoformat(last_ts) if last_ts else None

    return UniverseOut(
        symbols=setting.universe_json.get("top_symbols", []),
        ranked=ranked,
        source=setting.universe_json.get("selection_basis", "30d_quote_volume"),
        updated_at=updated_at,
    )
