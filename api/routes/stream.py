"""Authenticated SSE backed by a durable PostgreSQL log, with cursor replay."""
import asyncio
import json
import time
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import noload
from sqlalchemy.ext.asyncio import AsyncSession

from core.progress import events_after, progress_payload
from db.crud import get_run_by_id
from db.database import get_db, AsyncSessionLocal
from db.models import EvalRun, RunStatus

router = APIRouter()
HEARTBEAT_INTERVAL_SECONDS = 10
POLL_INTERVAL_SECONDS = .25


def encode_event(kind, data, event_id=None):
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return f"{prefix}event: {kind}\ndata: {json.dumps(data, allow_nan=False)}\n\n"


async def eval_event_generator(run_id, request, after=0):
    """Replay all retained events after the cursor, then tail until terminal.

    No dependency session/connection is held across yields. Polling the durable
    log avoids subscribe/publish races and continues when Redis pub/sub is down.
    """
    yield "retry: 1000\n\n"
    heartbeat = time.monotonic()
    while not await request.is_disconnected():
        async with AsyncSessionLocal() as db:
            rows = await events_after(db, run_id, after)
            terminal = None
            if not rows:
                run = (await db.execute(select(EvalRun).options(noload("*")).where(EvalRun.id == UUID(str(run_id))))).scalar_one_or_none()
                if run is None:
                    return
                if run.status in (RunStatus.COMPLETE, RunStatus.FAILED):
                    terminal = progress_payload(run)
        for row in rows:
            if await request.is_disconnected():
                return
            after = row.id
            yield encode_event(row.kind, row.data, row.id)
            heartbeat = time.monotonic()
            if row.kind in ("done", "error"):
                return
        if terminal:
            # State commit can precede event append or a process can die
            # between them. A terminal snapshot is available even then.
            yield encode_event("done" if terminal.status == "complete" else "error", terminal.model_dump(mode="json"))
            return
        if time.monotonic() - heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
            yield encode_event("ping", {})
            heartbeat = time.monotonic()
        if not rows:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


@router.get("/{run_id}", summary="Replay and stream evaluation progress")
async def stream_run(run_id: UUID, request: Request,
                     last_event_id: str | None = Header(None, alias="Last-Event-ID"),
                     db: AsyncSession = Depends(get_db)):
    try:
        after = int(last_event_id) if last_event_id else 0
        if not 0 <= after <= 2**63 - 1:
            raise ValueError()
    except ValueError:
        raise HTTPException(status_code=422, detail="Last-Event-ID must be a nonnegative integer")
    run = await get_run_by_id(db, str(run_id))
    if run is None:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
    # Release the initial lookup connection before opening a long response.
    await db.close()
    return StreamingResponse(eval_event_generator(str(run_id), request, after), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
