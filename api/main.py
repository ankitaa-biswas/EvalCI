# evalci/api/main.py
# Purpose: FastAPI application entry point. Initialises the app, registers all
# route blueprints, sets up CORS, startup/shutdown event hooks (DB connection
# pool, Redis, Celery), and mounts the static dashboard files.

from contextlib import asynccontextmanager

from fastapi import FastAPI
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
    """
    Manage application startup and shutdown events.

    On startup:
        - Initialise the PostgreSQL connection pool via SQLAlchemy.
        - Verify Redis connectivity.
        - Verify Celery worker reachability.

    On shutdown:
        - Gracefully close the DB connection pool.
        - Flush any pending Redis operations.
    """
    logger.info("EvalCI starting up — initialising database connection pool.")
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
