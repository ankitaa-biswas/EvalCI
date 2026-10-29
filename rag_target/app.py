"""HTTP RAG target, indexed before readiness succeeds."""
import asyncio
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from rag_target.service import RAGService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    service = RAGService()
    await asyncio.to_thread(service.initialize)
    app.state.rag = service
    yield


app = FastAPI(title="EvalCI RAG target", lifespan=lifespan)


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10000)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    try:
        if not await asyncio.to_thread(app.state.rag.ready):
            raise RuntimeError("Empty index")
        return {"status": "ready", "mode": app.state.rag.mode}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready"})


@app.post("/query")
async def query(payload: QueryRequest):
    try:
        return await app.state.rag.query(payload.query)
    except Exception as exc:
        logger.exception("RAG query failed")
        raise HTTPException(status_code=503, detail="RAG retrieval/generation failed; inspect rag-target logs and provider configuration") from exc


@app.get("/identity")
async def identity():
    from scripts.image_identity import read_identity
    return {**read_identity(), "mode": app.state.rag.mode}
