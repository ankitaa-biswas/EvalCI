# evalci/api/main.py
# Purpose: FastAPI application entry point. Initialises the app, registers all
# route blueprints, sets up CORS, startup/shutdown event hooks (DB connection
# pool, Redis, Celery), and mounts the static dashboard files.

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.middleware.auth import APIKeyMiddleware
from api.middleware.rate_limit import RateLimitMiddleware
from api.routes import evaluate, results, stream
from core.observability import get_logger, metrics
from db.database import init_db, close_db

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage DB lifecycle; /ready checks database, Redis and worker readiness."""
    logger.info("EvalCI starting up — initialising database connection pool.")
    import os
    if os.getenv("DB_AUTO_CREATE", "true").lower() == "true":
        await init_db()
    metrics.record_eval_started("system", "startup")
    logger.info("EvalCI startup complete — observability wired, accepting requests.")
    yield
    logger.info("EvalCI shutting down — closing database connection pool.")
    await close_db()
    logger.info("EvalCI shutdown complete.")


app = FastAPI(
    title="EvalCI",
    description=(
        "Automated evaluation and regression testing harness for RAG systems. "
        "Scores answers on correctness, grounding, and hallucination risk, and "
        "produces a Regression Fingerprint Report to attribute quality drops to "
        "specific pipeline components."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# Starlette applies middleware in reverse registration order, so the last
# middleware added runs first on incoming requests.
#
# Execution order on request (outermost → innermost):
#   1. CORSMiddleware      — handles OPTIONS pre-flight before auth fires
#   2. APIKeyMiddleware    — validates X-API-Key, sets request.state.api_key
#   3. RateLimitMiddleware — uses request.state.api_key as identifier
#
# Registration order (reversed): RateLimit first, then APIKey.
app.add_middleware(RateLimitMiddleware)
app.add_middleware(APIKeyMiddleware)

# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

app.include_router(evaluate.router, prefix="/evaluate", tags=["Evaluation"])
app.include_router(results.router, prefix="/results", tags=["Results"])
app.include_router(stream.router, prefix="/stream", tags=["Streaming"])

# ---------------------------------------------------------------------------
# Static files — serve the HTML dashboard
# ---------------------------------------------------------------------------

app.mount("/dashboard", StaticFiles(directory="dashboard", html=True), name="dashboard")


@app.get("/health", tags=["Health"])
async def health_check():
    """
    Liveness probe.

    Returns a simple JSON payload confirming the API is reachable.
    Used by Docker health-checks and GitHub Actions smoke tests.
    """
    return {"status": "ok", "service": "evalci"}


@app.get("/ready", tags=["Health"])
async def readiness_check():
    """Readiness requires the migrated database, Redis and a reachable worker."""
    import asyncio
    from sqlalchemy import text
    from core.cache import get_redis_client
    from core.queue import celery_app
    from db.database import AsyncSessionLocal
    redis = get_redis_client()
    checks = {}
    try:
        async with AsyncSessionLocal() as db:
            revision = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
            if revision != "c2_progress_events":
                raise RuntimeError("Run alembic upgrade head before serving evaluations")
        checks["database"] = True
        checks["redis"] = bool(await redis.ping())
        checks["worker"] = bool(await asyncio.to_thread(lambda: celery_app.control.inspect(timeout=2).ping()))
        if not all(checks.values()):
            raise RuntimeError("A required dependency is not ready")
        return {"status": "ready", "checks": checks, "evaluation_mode": __import__("os").getenv("EVAL_SCORING_MODE", "ragas")}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready", "checks": checks})
    finally:
        await redis.aclose()


@app.get("/identity", tags=["Health"])
async def identity():
    from scripts.image_identity import read_identity
    return read_identity()
