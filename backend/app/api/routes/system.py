from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, get_current_user
from app.core.events import publish_event
from app.db.session import get_db
from app.models.entities import EquitySnapshot, LogEntry, Order, Position, Trade, User
from app.schemas.common import Message
from app.services.settings_service import ensure_user_settings

router = APIRouter(prefix="/system")


class PaperResetRequest(BaseModel):
    limit_usd: float = Field(default=10000.0, gt=0)


@router.post("/pause", response_model=Message)
def pause_system(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Message:
    setting = ensure_user_settings(db, user.id)
    setting.kill_switch_paused = True
    db.commit()
    publish_event("kill_switch", {"mode": "manual", "reason": "manual_pause"})
    return Message(message="Trading paused")


@router.post("/resume", response_model=Message)
def resume_system(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Message:
    setting = ensure_user_settings(db, user.id)
    setting.kill_switch_paused = False
    db.commit()
    publish_event("kill_switch", {"mode": "manual", "reason": "manual_resume"})
    return Message(message="Trading resumed")


@router.get("/logs")
def get_system_logs(
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
) -> list[dict]:
    rows = (
        db.query(LogEntry)
        .order_by(LogEntry.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": row.id,
            "level": row.level,
            "component": row.component,
            "message": row.message,
            "context_json": row.context_json,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


@router.post("/paper/reset", response_model=Message)
def reset_paper_state(
    payload: PaperResetRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Message:
    if payload.limit_usd <= 0:
        raise HTTPException(status_code=400, detail="limit_usd must be greater than 0")

    setting = ensure_user_settings(db, user.id)

    deleted_positions = db.execute(delete(Position)).rowcount or 0
    deleted_trades = db.execute(delete(Trade)).rowcount or 0
    deleted_orders = db.execute(delete(Order)).rowcount or 0
    deleted_equity = db.execute(delete(EquitySnapshot)).rowcount or 0

    risk = dict(setting.risk_params_json or {})
    risk["initial_equity"] = float(payload.limit_usd)
    risk["max_position_notional_pct"] = 100.0
    setting.risk_params_json = risk

    db.commit()

    publish_event(
        "paper_reset",
        {
            "limit_usd": float(payload.limit_usd),
            "deleted_positions": int(deleted_positions),
            "deleted_trades": int(deleted_trades),
            "deleted_orders": int(deleted_orders),
            "deleted_equity_snapshots": int(deleted_equity),
            "by_user_id": user.id,
        },
    )

    return Message(
        message=(
            f"Paper state reset. Limit set to ${payload.limit_usd:,.2f}. "
            f"Deleted positions={deleted_positions}, trades={deleted_trades}, "
            f"orders={deleted_orders}, equity_snapshots={deleted_equity}."
        )
    )
