from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)
EVENT_CHANNEL = "trader:events"


def publish_event(event_type: str, payload: dict[str, Any]) -> None:
    try:
        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        message = {
            "type": event_type,
            "ts": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        client.publish(EVENT_CHANNEL, json.dumps(message))
    except Exception as exc:
        logger.error("failed_to_publish_event", extra={"context": {"error": str(exc)}})
