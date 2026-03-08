from __future__ import annotations

import json
from datetime import datetime, timezone

import redis

from app.core.config import settings

SYSTEM_PAUSE_KEY = "trader:system:paused"
BACKFILL_STATUS_KEY = "trader:backfill:status"
BACKFILL_ACTIVE_TTL_SECONDS = 900


def _client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def set_system_paused(paused: bool) -> None:
    client = _client()
    client.set(SYSTEM_PAUSE_KEY, "1" if paused else "0")


def is_system_paused() -> bool:
    try:
        client = _client()
        value = client.get(SYSTEM_PAUSE_KEY)
        return value == "1"
    except Exception:
        return False


def mark_backfill_running() -> None:
    payload = {
        "state": "running",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        _client().set(BACKFILL_STATUS_KEY, json.dumps(payload), ex=BACKFILL_ACTIVE_TTL_SECONDS)
    except Exception:
        return


def mark_backfill_finished(status: str, details: dict | None = None) -> None:
    payload = {
        "state": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "details": details or {},
    }
    try:
        _client().set(BACKFILL_STATUS_KEY, json.dumps(payload), ex=BACKFILL_ACTIVE_TTL_SECONDS)
    except Exception:
        return


def get_backfill_status() -> dict:
    fallback = {"state": "unknown", "updated_at": None, "details": {}}
    try:
        raw = _client().get(BACKFILL_STATUS_KEY)
        if not raw:
            return fallback
        data = json.loads(raw)
        if not isinstance(data, dict):
            return fallback
        return {
            "state": str(data.get("state") or "unknown"),
            "updated_at": data.get("updated_at"),
            "details": data.get("details") if isinstance(data.get("details"), dict) else {},
        }
    except Exception:
        return fallback
