from __future__ import annotations

import redis

from app.core.config import settings

SYSTEM_PAUSE_KEY = "trader:system:paused"


def set_system_paused(paused: bool) -> None:
    client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    client.set(SYSTEM_PAUSE_KEY, "1" if paused else "0")


def is_system_paused() -> bool:
    try:
        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        value = client.get(SYSTEM_PAUSE_KEY)
        return value == "1"
    except Exception:
        return False
