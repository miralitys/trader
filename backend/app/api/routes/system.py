from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, get_current_user
from app.core.events import publish_event
from app.db.session import get_db
from app.models.entities import LogEntry, User
from app.schemas.common import Message
from app.services.settings_service import ensure_user_settings

router = APIRouter(prefix="/system")


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
