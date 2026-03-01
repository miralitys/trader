from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import redis.asyncio as redis
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.core.events import EVENT_CHANNEL

router = APIRouter(prefix="/realtime")


async def event_stream():
    client = redis.from_url(settings.redis_url, decode_responses=True)
    pubsub = client.pubsub(ignore_subscribe_messages=True)
    await pubsub.subscribe(EVENT_CHANNEL)

    try:
        while True:
            message = await pubsub.get_message(timeout=10.0)
            if message and message.get("type") == "message":
                payload = message["data"]
                parsed = json.loads(payload)
                event_type = parsed.get("type", "message")
                yield f"event: {event_type}\n"
                yield f"data: {json.dumps(parsed, ensure_ascii=True)}\n\n"
            else:
                heartbeat = {
                    "type": "heartbeat",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "payload": {"status": "alive"},
                }
                yield "event: heartbeat\n"
                yield f"data: {json.dumps(heartbeat, ensure_ascii=True)}\n\n"
                await asyncio.sleep(5)
    finally:
        await pubsub.unsubscribe(EVENT_CHANNEL)
        await pubsub.close()
        await client.close()


@router.get("/sse")
async def sse_endpoint():
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)
