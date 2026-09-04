# evalci/api/routes/stream.py
# GET /stream/{run_id} — Server-Sent Events live score updates.

import asyncio
import json
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.cache import get_redis_client, PUBSUB_CHANNEL_PREFIX
from db.crud import get_run_by_id
from db.database import get_db
from db.models import RunStatus

router = APIRouter()

HEARTBEAT_INTERVAL_SECONDS = 15
TERMINAL_STATES = {RunStatus.COMPLETE, RunStatus.FAILED}


async def eval_event_generator(
    run_id: str,
    request: Request,
) -> AsyncGenerator[str, None]:
    """
    Async generator streaming SSE events for a given run.

    Subscribes to Redis pub/sub channel evalci:run:{run_id}.
    Yields SSE-formatted strings. Sends heartbeat pings every 15 seconds.
    Terminates on: client disconnect, done/error event, or timeout.
    """
    import redis.asyncio as aioredis
    from core.cache import REDIS_URL

    # Create a dedicated connection for subscriptions
    redis = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
    channel = f"{PUBSUB_CHANNEL_PREFIX}{run_id}"

    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)

    try:
        while True:
            # Check for client disconnect
            if await request.is_disconnected():
                break

            # Wait for a message (with a short timeout so we can check disconnect)
            try:
                message = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5),
                    timeout=HEARTBEAT_INTERVAL_SECONDS,
                )
            except asyncio.TimeoutError:
                # Send heartbeat ping
                yield "event: ping\ndata: {}\n\n"
                continue

            if message is None:
                # No message yet — short sleep and check again
                await asyncio.sleep(0.1)
                continue

            if message.get("type") != "message":
                continue

            raw = message.get("data", "")
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            event_type = payload.get("type", "message")
            data = json.dumps(payload.get("data", {}))
            yield f"event: {event_type}\ndata: {data}\n\n"

            # Terminate when run reaches a terminal state
            if event_type in ("done", "error"):
                break

    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        await redis.aclose()


@router.get(
    "/{run_id}",
    summary="Subscribe to live SSE score updates for an evaluation run",
)
async def stream_run(
    run_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Open a Server-Sent Events stream for the given run_id.
    Validates the run exists before opening the stream.
    """
    run = await get_run_by_id(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # Disable nginx buffering
        "Connection": "keep-alive",
    }

    return StreamingResponse(
        eval_event_generator(run_id, request),
        media_type="text/event-stream",
        headers=headers,
    )
