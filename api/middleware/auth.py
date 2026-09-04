"""
api/middleware/auth.py
──────────────────────
Starlette middleware for API key authentication.

Design
──────
API keys are read from the ``EVALCI_API_KEYS`` environment variable as a
comma-separated list.  Every request (except a small set of public paths)
must supply a valid key in the ``X-API-Key`` header.

Dev mode
────────
If ``EVALCI_API_KEYS`` is not set or resolves to an empty set after stripping,
the middleware logs a one-time WARNING and allows **all** requests through.
This makes local development friction-free while keeping production safe.

Public (unauthenticated) paths
──────────────────────────────
The following ``(method, path)`` pairs bypass key validation:

    GET /health
    GET /docs
    GET /openapi.json
    GET /redoc

Usage
──────
Register in ``api/main.py``::

    from api.middleware.auth import APIKeyMiddleware
    app.add_middleware(APIKeyMiddleware)
"""

from __future__ import annotations

import logging
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths that never require an API key (method is compared case-insensitively)
# ---------------------------------------------------------------------------

_PUBLIC_PATHS: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/health"),
        ("GET", "/docs"),
        ("GET", "/openapi.json"),
        ("GET", "/redoc"),
    }
)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Validate ``X-API-Key`` header on every non-public request.

    Attributes:
        _api_keys: Immutable set of accepted API key strings, built once at
            startup from the ``EVALCI_API_KEYS`` environment variable.
        _dev_mode: When ``True``, all requests are allowed through regardless
            of the ``X-API-Key`` header.  Activated when ``EVALCI_API_KEYS``
            is absent or empty.
    """

    def __init__(self, app) -> None:
        """Initialise middleware and resolve the allowed key set.

        Args:
            app: The ASGI application to wrap (passed through to
                ``BaseHTTPMiddleware``).
        """
        super().__init__(app)

        # ── Step 1: Read and parse EVALCI_API_KEYS ────────────────────────────
        raw = os.environ.get("EVALCI_API_KEYS", "")
        keys: set[str] = {k.strip() for k in raw.split(",") if k.strip()}
        self._api_keys: frozenset[str] = frozenset(keys)

        # ── Step 5: Dev mode when no keys are configured ─────────────────────
        if not self._api_keys:
            logger.warning(
                "EVALCI_API_KEYS is not set or empty — running in dev mode. "
                "All requests are allowed through without authentication. "
                "Set EVALCI_API_KEYS in production."
            )
            self._dev_mode: bool = True
        else:
            logger.info(
                "APIKeyMiddleware: %d API key(s) loaded from EVALCI_API_KEYS.",
                len(self._api_keys),
            )
            self._dev_mode = False

    async def dispatch(self, request: Request, call_next):
        """Authenticate each incoming request.

        Args:
            request:   The incoming Starlette ``Request`` object.
            call_next: Callable that forwards the request to the next layer.

        Returns:
            A ``JSONResponse`` with HTTP 401 if authentication fails, or the
            response produced by the downstream application on success.
        """
        # ── Step 5: Dev mode — skip all auth ─────────────────────────────────
        if self._dev_mode:
            return await call_next(request)

        # ── Step 2: Skip auth for public paths ───────────────────────────────
        method = request.method.upper()
        path = request.url.path
        if (method, path) in _PUBLIC_PATHS:
            return await call_next(request)

        # ── Step 3: Validate X-API-Key header ────────────────────────────────
        api_key = request.headers.get("X-API-Key", "")
        if not api_key or api_key not in self._api_keys:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Unauthorized",
                    "detail": "Valid X-API-Key header required",
                },
            )

        # ── Step 4: Attach key to request state and forward ───────────────────
        request.state.api_key = api_key
        return await call_next(request)
