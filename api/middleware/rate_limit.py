"""
api/middleware/rate_limit.py
────────────────────────────
Starlette middleware for per-requester, per-minute rate limiting backed by Redis.

Design
──────
Each requester is identified by their API key (set on ``request.state.api_key``
by ``APIKeyMiddleware``) or, for unauthenticated/dev-mode traffic, by their
client IP address.  A sliding-window counter is maintained in Redis using the
key pattern::

    evalci:rate:{identifier}:{current_minute}

where ``current_minute = int(time.time() // 60)``.  The TTL of 90 seconds
ensures keys are cleaned up within at most one extra minute after the window
they belong to expires, without creating gaps at the boundary.

Fail-open behaviour
───────────────────
If Redis is unreachable for any reason the middleware logs an error and
allows the request through.  Rate-limiting is a best-effort guardrail; it
must never become a single point of failure that blocks all traffic.

Public (unauthenticated) paths
──────────────────────────────
The following ``(method, path)`` pairs bypass rate limiting:

    GET /health
    GET /docs
    GET /openapi.json
    GET /redoc

Environment variables
─────────────────────
``REDIS_URL``
    Connection URL passed to ``redis.asyncio.from_url``.
    Default: ``redis://localhost:6379``.

``RATE_LIMIT_PER_MINUTE``
    Maximum requests allowed per identifier per 60-second window.
    Default: ``60``.

Usage
──────
Register in ``api/main.py`` **after** ``APIKeyMiddleware`` so that
``request.state.api_key`` is already populated::

    from api.middleware.rate_limit import RateLimitMiddleware
    app.add_middleware(RateLimitMiddleware)
"""

from __future__ import annotations

import logging
import os
import time

import redis.asyncio as aioredis
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths that are exempt from rate limiting
# ---------------------------------------------------------------------------

_PUBLIC_PATHS: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/health"),
        ("GET", "/docs"),
        ("GET", "/openapi.json"),
        ("GET", "/redoc"),
    }
)

# Redis key prefix and TTL constants
_KEY_PREFIX: str = "evalci:rate"
_KEY_TTL_SECONDS: int = 90  # outlives the 60-second window by 30 s


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforce per-requester, per-minute request rate limits via Redis.

    Attributes:
        _redis_url:   Redis connection URL resolved at startup.
        _limit:       Maximum requests allowed per identifier per minute.
        _redis:       Lazy ``redis.asyncio`` client; created on first use.
    """

    def __init__(self, app) -> None:
        """Initialise middleware and read configuration from the environment.

        Args:
            app: The ASGI application to wrap.
        """
        super().__init__(app)

        # ── Step 1: Read configuration ────────────────────────────────────────
        self._redis_url: str = os.environ.get(
            "REDIS_URL", "redis://localhost:6379"
        )

        raw_limit = os.environ.get("RATE_LIMIT_PER_MINUTE", "60")
        try:
            self._limit: int = int(raw_limit)
        except ValueError:
            logger.warning(
                "RATE_LIMIT_PER_MINUTE value %r is not a valid integer — "
                "falling back to default of 60.",
                raw_limit,
            )
            self._limit = 60

        # Lazy client — created on first request to avoid blocking at import.
        self._redis: aioredis.Redis | None = None

        logger.info(
            "RateLimitMiddleware: limit=%d req/min, Redis=%s",
            self._limit,
            self._redis_url,
        )

    def _get_redis(self) -> aioredis.Redis:
        """Return a cached async Redis client, creating it on first call.

        Returns:
            An ``aioredis.Redis`` instance connected to ``_redis_url``.
        """
        if self._redis is None:
            self._redis = aioredis.from_url(
                self._redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._redis

    # ------------------------------------------------------------------
    # Core dispatch
    # ------------------------------------------------------------------

    async def dispatch(self, request: Request, call_next):
        """Apply rate limiting to every non-exempt request.

        Args:
            request:   The incoming Starlette ``Request``.
            call_next: Callable that forwards the request downstream.

        Returns:
            HTTP 429 ``JSONResponse`` when the limit is exceeded, or the
            downstream response on success / Redis failure.
        """
        # ── Step 2: Skip exempt paths ─────────────────────────────────────────
        method = request.method.upper()
        path = request.url.path
        if (method, path) in _PUBLIC_PATHS:
            return await call_next(request)

        # ── Step 3: Identify the requester ────────────────────────────────────
        identifier = self._get_identifier(request)

        # ── Steps 4–6: Count and enforce ──────────────────────────────────────
        try:
            exceeded, count = await self._check_and_increment(identifier)
        except Exception as exc:
            # ── Step 6: Fail-open on Redis error ─────────────────────────────
            logger.error(
                "RateLimitMiddleware: Redis error for identifier=%r — "
                "allowing request through. Error: %s",
                identifier,
                exc,
            )
            return await call_next(request)

        if exceeded:
            retry_after = self._seconds_until_next_minute()
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "limit": self._limit,
                    "retry_after": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)

    # ------------------------------------------------------------------
    # Step 3 — Identifier resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _get_identifier(request: Request) -> str:
        """Resolve the rate-limit identifier for the request.

        Prefers ``request.state.api_key`` (set by ``APIKeyMiddleware``) so
        that all traffic from the same key shares a single counter regardless
        of client IP.  Falls back to the client IP for dev-mode or unkeyed
        traffic.

        Args:
            request: The incoming request.

        Returns:
            A non-empty string identifying the requester.
        """
        api_key: str | None = getattr(request.state, "api_key", None)
        if api_key:
            return api_key
        # request.client can be None in some test environments
        host = request.client.host if request.client else "unknown"
        return host

    # ------------------------------------------------------------------
    # Steps 4 & 5 — Redis counter logic
    # ------------------------------------------------------------------

    async def _check_and_increment(self, identifier: str) -> tuple[bool, int]:
        """Atomically increment the per-minute counter in Redis.

        Uses INCR + EXPIRE (on first creation) rather than a Lua script so
        the logic stays simple and readable.  The 90-second TTL means the key
        survives for one full extra window, preventing any cleanup race.

        Args:
            identifier: The resolved requester identifier.

        Returns:
            A ``(exceeded, count)`` tuple where ``exceeded`` is ``True`` when
            the counter has surpassed ``_limit`` and ``count`` is the current
            counter value after the increment.

        Raises:
            Any exception raised by the Redis client (caught by ``dispatch``).
        """
        # ── Step 4: Build the Redis key ───────────────────────────────────────
        current_minute = int(time.time() // 60)
        redis_key = f"{_KEY_PREFIX}:{identifier}:{current_minute}"

        client = self._get_redis()

        # Increment counter; returns the new value.
        count: int = await client.incr(redis_key)

        # Set TTL only on the first increment (count == 1) so we don't
        # keep pushing the expiry forward on every request.
        if count == 1:
            await client.expire(redis_key, _KEY_TTL_SECONDS)

        exceeded = count > self._limit
        return exceeded, count

    # ------------------------------------------------------------------
    # Step 5 helper — retry-after calculation
    # ------------------------------------------------------------------

    @staticmethod
    def _seconds_until_next_minute() -> int:
        """Calculate whole seconds remaining until the next 60-second window.

        Returns:
            An integer in [1, 60] representing the number of seconds the
            client should wait before retrying.
        """
        now = time.time()
        next_minute = (int(now // 60) + 1) * 60
        return max(1, int(next_minute - now))
