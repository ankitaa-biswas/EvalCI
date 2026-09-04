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
from db.database import init_db, close_db


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
    await init_db()
    yield
    await close_db()


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

# API key authentication — reads EVALCI_API_KEYS from env (comma-separated).
# Runs after CORS so OPTIONS pre-flight requests are not blocked.
app.add_middleware(APIKeyMiddleware)

# Per-requester rate limiting — reads REDIS_URL and RATE_LIMIT_PER_MINUTE from
# env. Registered after APIKeyMiddleware so that request.state.api_key is
# available as the rate-limit identifier for keyed traffic.
app.add_middleware(RateLimitMiddleware)

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
