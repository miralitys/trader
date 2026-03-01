from __future__ import annotations

from datetime import datetime, timezone

import redis
from fastapi import APIRouter, Depends
from sqlalchemy import text, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.entities import Setting, User
from app.schemas.health import HealthOut
from app.services.market_data import get_last_sync_info

router = APIRouter()


@router.get("/health", response_model=HealthOut)
def health(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> HealthOut:
    db_ok = False
    redis_ok = False

    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    try:
        redis.Redis.from_url(settings.redis_url, decode_responses=True).ping()
        redis_ok = True
    except Exception:
        redis_ok = False

    setting = db.scalar(select(Setting).order_by(Setting.id.asc()).limit(1))
    last_sync, delay = get_last_sync_info()

    return HealthOut(
        status="ok" if db_ok and redis_ok else "degraded",
        paper_enabled=bool(setting.paper_enabled) if setting else settings.paper_enabled,
        live_enabled=bool(setting.live_enabled) if setting else settings.live_enabled,
        kill_switch_paused=bool(setting.kill_switch_paused) if setting else False,
        redis_ok=redis_ok,
        db_ok=db_ok,
        server_time=datetime.now(timezone.utc),
        last_data_sync_at=last_sync,
        data_delay_seconds=delay,
    )
