from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class HealthOut(BaseModel):
    status: str
    paper_enabled: bool
    live_enabled: bool
    kill_switch_paused: bool
    redis_ok: bool
    db_ok: bool
    server_time: datetime
    last_data_sync_at: datetime | None
    data_delay_seconds: int | None
