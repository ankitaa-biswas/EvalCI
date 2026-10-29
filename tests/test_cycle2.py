"""Offline tests: real durable event storage; RAG/model calls are mocked."""
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import httpx
import pandas as pd
import pytest
from sqlalchemy import select

from api.middleware.auth import APIKeyMiddleware
from api.routes import stream
from core.evaluator import RAGEvaluator
from core.progress import ProgressEvent, append_event, events_after, progress_payload
from db.crud import create_run, get_run_by_id
from db.database import get_db
from db.models import RunEvent, RunStatus
from rag_target.service import FixtureEmbeddings
from tests.test_cycle1 import storage, client, pipeline, scoring_env


@pytest.mark.asyncio
async def test_progress_is_durable_during_requests_and_scoring(pipeline, storage):
    submit, _ = pipeline
    run_id = await submit()
    async with storage() as db:
        rows = await events_after(db, run_id)
        rows += await events_after(db, run_id, rows[-1].id)
    stages = [r.data["stage"] for r in rows]
    assert stages[0] == "queued" and stages[-1] == "complete"
    assert rows[-1].kind == "done"
    for stage in ("rag_requests", "scoring"):
        progress = [r for r in rows if r.data["stage"] == stage]
        assert any(0 < r.data["completed"] < r.data["total"] for r in progress)
        assert [r.data["completed"] for r in progress] == sorted(r.data["completed"] for r in progress)
        assert all(r.id < rows[-1].id for r in progress)
    assert all(ProgressEvent.model_validate(r.data) for r in rows)


@pytest.mark.asyncio
async def test_sse_authenticated_replay_and_terminal_snapshot(pipeline, storage, monkeypatch):
    submit, _ = pipeline
    run_id = await submit()
    monkeypatch.setattr(stream, "AsyncSessionLocal", storage)
    monkeypatch.setenv("EVALCI_API_KEYS", "unit-test-key")
    app = FastAPI()
    app.include_router(stream.router, prefix="/stream")
    app.mount("/dashboard", StaticFiles(directory=str(Path(__file__).resolve().parents[1]/"dashboard"), html=True))
    app.add_middleware(APIKeyMiddleware)
    async def database():
        async with storage() as db: yield db
    app.dependency_overrides[get_db] = database
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(f"/stream/{run_id}")).status_code == 401
        assert (await client.get("/dashboard/")).status_code == 200
        headers = {"X-API-Key":"unit-test-key"}
        response = await client.get(f"/stream/{run_id}", headers=headers)
        assert response.status_code == 200
        assert "event: done" in response.text
        ids = [int(line[4:]) for line in response.text.splitlines() if line.startswith("id: ")]
        assert ids == sorted(set(ids)) and len(ids) > 100
        cursor = ids[20]
        response = await client.get(f"/stream/{run_id}", headers={**headers,"Last-Event-ID":str(cursor)})
        replayed = [int(line[4:]) for line in response.text.splitlines() if line.startswith("id: ")]
        assert replayed and min(replayed) > cursor and "event: done" in response.text
        response = await client.get(f"/stream/{run_id}", headers={**headers,"Last-Event-ID":str(ids[-1])})
        assert "event: done" in response.text  # Cursor already includes terminal.
        assert (await client.get(f"/stream/{run_id}", headers={**headers,"Last-Event-ID":"bad"})).status_code == 422


class Disconnectable:
    disconnected = False
    async def is_disconnected(self): return self.disconnected


@pytest.mark.asyncio
async def test_stream_yields_before_terminal_and_disconnects(storage, monkeypatch):
    monkeypatch.setattr(stream, "AsyncSessionLocal", storage)
    async with storage() as db:
        run = await create_run(db,"a"*40,"test")
        run.completion = {"expected":2,"attempted":0,"successfully_scored":0,"failed":0}
        await db.commit()
        row = await append_event(db,"progress",progress_payload(run,"queued"))
        run_id = str(run.id)
    request = Disconnectable()
    gen = stream.eval_event_generator(run_id,request)
    assert await anext(gen) == "retry: 1000\n\n"
    assert f"id: {row.id}" in await anext(gen)
    async with storage() as db:
        event = ProgressEvent(run_id=run_id,stage="rag_requests",completed=1,total=2,status="running",attempted=1,successfully_scored=0,failed=0)
        await append_event(db,"progress",event)
    assert '"completed": 1' in await asyncio.wait_for(anext(gen), 2)
    request.disconnected = True
    with pytest.raises(StopAsyncIteration): await anext(gen)


@pytest.mark.asyncio
async def test_terminal_fallback_without_published_event(storage, monkeypatch):
    monkeypatch.setattr(stream,"AsyncSessionLocal",storage)
    async with storage() as db:
        run = await create_run(db,"a"*40,"test")
        run.completion = {"expected":2,"attempted":2,"successfully_scored":0,"failed":2}
        run.error_message = "Synthetic unreachable RAG target"
        run.status = RunStatus.FAILED
        await db.commit()
        run_id = str(run.id)
    gen = stream.eval_event_generator(run_id,Disconnectable())
    await anext(gen)
    packet = await anext(gen)
    assert "event: error" in packet and "unreachable" in packet
    with pytest.raises(StopAsyncIteration): await anext(gen)


def test_progress_counter_validation():
    with pytest.raises(ValueError):
        ProgressEvent(run_id=uuid4(),stage="scoring",completed=4,total=3,status="running",attempted=3,successfully_scored=4,failed=0)


def test_fixture_embedding_is_deterministic_and_normalized():
    embedder = FixtureEmbeddings()
    vector = embedder.embed_query("retrieval vector database")
    assert vector == embedder.embed_query("retrieval vector database")
    assert len(vector) == 128 and sum(v*v for v in vector) == pytest.approx(1)
