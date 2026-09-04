"""
core/observability.py
─────────────────────
Structured logging and lightweight metrics collection for EvalCI.

Two concerns are unified here so that every module in the codebase has a
single import for both human-readable logs and machine-readable metrics:

1. **JSONFormatter / get_logger** — wraps Python's ``logging`` module to emit
   every log record as a single, self-contained JSON line on stdout.  This
   makes logs trivially parseable by log aggregators (Datadog, CloudWatch,
   Loki, etc.) without any external library.

2. **MetricsCollector** — appends one JSON event per call to
   ``logs/metrics.jsonl``, creating the file and its parent directory if they
   do not exist.  Each event is also forwarded through the JSON logger so
   that structured metrics appear in the same stdout stream as application
   logs.

Usage::

    from core.observability import get_logger, metrics

    logger = get_logger(__name__)
    logger.info("Fingerprinting complete", extra={"run_id": run_id})

    metrics.record_eval_started(run_id, commit_sha)
    metrics.record_eval_duration(run_id, elapsed)

Module-level singleton
──────────────────────
``metrics`` is a ready-to-use ``MetricsCollector`` instance created at import
time.  Import it directly rather than constructing a new one:

    from core.observability import metrics

Dependencies: Python standard library only (``logging``, ``json``, ``os``,
``datetime``, ``pathlib``).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SERVICE_NAME: str = "evalci"

#: Path to the append-only metrics event log, relative to the process CWD.
#: Override via the ``EVALCI_METRICS_PATH`` environment variable if needed.
_METRICS_PATH: Path = Path(
    os.environ.get("EVALCI_METRICS_PATH", "logs/metrics.jsonl")
)


# ---------------------------------------------------------------------------
# Step 1 — JSON log formatter
# ---------------------------------------------------------------------------


class JSONFormatter(logging.Formatter):
    """Format every log record as a single JSON object on one line.

    The emitted JSON always contains:

    - ``timestamp`` — ISO-8601 UTC timestamp with millisecond precision.
    - ``level``     — Log level name (``INFO``, ``WARNING``, etc.).
    - ``service``   — Hardcoded ``"evalci"`` for log routing/filtering.
    - ``message``   — The formatted log message string.
    - ``logger``    — The logger name (usually ``__name__`` of the caller).

    Any extra key/value pairs passed via ``extra={...}`` to the logging call
    are merged into the top-level JSON object, making it easy to attach
    structured context (``run_id``, ``severity``, etc.) without string
    interpolation.

    Example output (one line)::

        {"timestamp": "2026-09-04T10:30:00.123Z", "level": "INFO",
         "service": "evalci", "logger": "core.fingerprinter",
         "message": "Fingerprint computed", "run_id": "abc-123"}
    """

    #: Keys that are part of the standard LogRecord and should not be
    #: re-emitted as extra fields to avoid redundancy.
    _RESERVED: frozenset[str] = frozenset(
        {
            "args", "created", "exc_info", "exc_text", "filename",
            "funcName", "levelname", "levelno", "lineno", "message",
            "module", "msecs", "msg", "name", "pathname", "process",
            "processName", "relativeCreated", "stack_info", "taskName",
            "thread", "threadName",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        """Serialise *record* to a single-line JSON string.

        Args:
            record: The ``LogRecord`` to format.

        Returns:
            A JSON-encoded string (no trailing newline).
        """
        # Base payload — always present
        payload: dict = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(record.msecs):03d}Z",
            "level":   record.levelname,
            "service": _SERVICE_NAME,
            "logger":  record.name,
            "message": record.getMessage(),
        }

        # Merge any extra fields the caller attached via extra={...}
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                payload[key] = value

        # Attach exception info if present
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


# ---------------------------------------------------------------------------
# Step 2 — Logger factory
# ---------------------------------------------------------------------------


def get_logger(name: str) -> logging.Logger:
    """Return a ``logging.Logger`` that writes JSON lines to stdout.

    Calling this function multiple times with the same *name* returns the
    same ``Logger`` instance (standard Python logging behaviour) without
    adding duplicate handlers.

    Args:
        name: Logger name, typically ``__name__`` of the calling module.

    Returns:
        A configured ``logging.Logger`` instance.
    """
    logger = logging.getLogger(name)

    # Avoid adding duplicate handlers if get_logger is called multiple times.
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.propagate = False  # Prevent double-emission via root logger

    # Respect LOG_LEVEL env var (default INFO).
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, level_name, logging.INFO))

    return logger


# ---------------------------------------------------------------------------
# Step 3 — Metrics collector
# ---------------------------------------------------------------------------

_collector_logger = get_logger("core.observability.metrics")


class MetricsCollector:
    """Append-only structured metrics writer for EvalCI evaluation events.

    Each public method records a named event by:

    1. Constructing a flat dict with ``event``, ``run_id``, ``timestamp``,
       and method-specific fields.
    2. Appending the JSON-serialised dict as one line to ``logs/metrics.jsonl``
       (creating the file and directory if absent).
    3. Emitting the same payload through the JSON logger at ``INFO`` level so
       it appears in the unified stdout log stream.

    All I/O errors during file writes are caught and logged as warnings so
    that a metrics write failure never propagates to the caller.

    Attributes:
        _path: ``pathlib.Path`` pointing to the metrics JSONL file.
    """

    def __init__(self, path: Path = _METRICS_PATH) -> None:
        """Initialise the collector and ensure the metrics directory exists.

        Args:
            path: Path to the ``metrics.jsonl`` file.  Defaults to the
                module-level ``_METRICS_PATH`` constant which can be
                overridden via ``EVALCI_METRICS_PATH``.
        """
        self._path: Path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Step 4 — Event writers
    # ------------------------------------------------------------------

    def record_eval_started(self, run_id: str, commit_sha: str) -> None:
        """Record that an evaluation run has been dispatched.

        Args:
            run_id:     UUID of the newly created ``EvalRun``.
            commit_sha: Git commit SHA being evaluated.
        """
        self._write(
            {
                "event":      "eval_started",
                "run_id":     run_id,
                "commit_sha": commit_sha,
            }
        )

    def record_eval_duration(self, run_id: str, seconds: float) -> None:
        """Record the wall-clock duration of a completed evaluation run.

        Args:
            run_id:  UUID of the completed ``EvalRun``.
            seconds: Total elapsed time from dispatch to completion.
        """
        self._write(
            {
                "event":   "eval_duration",
                "run_id":  run_id,
                "seconds": round(seconds, 3),
            }
        )

    def record_fingerprint_computed(
        self,
        run_id: str,
        dominant_failure: str,
        severity: float,
    ) -> None:
        """Record that a regression fingerprint has been computed.

        Args:
            run_id:           UUID of the ``EvalRun``.
            dominant_failure: The component attributed as the primary failure
                              source (``"retriever"``, ``"generator"``,
                              ``"prompt"``, or ``"kb"``).
            severity:         Severity score on the 0–10 scale.
        """
        self._write(
            {
                "event":            "fingerprint_computed",
                "run_id":           run_id,
                "dominant_failure": dominant_failure,
                "severity":         round(severity, 2),
            }
        )

    def record_gate_decision(
        self,
        run_id: str,
        passed: bool,
        reason: str,
    ) -> None:
        """Record the outcome of the CI quality gate check.

        Args:
            run_id: UUID of the ``EvalRun``.
            passed: ``True`` if the gate allowed the build to proceed.
            reason: Human-readable explanation of the gate decision.
        """
        self._write(
            {
                "event":  "gate_decision",
                "run_id": run_id,
                "passed": passed,
                "reason": reason,
            }
        )

    # ------------------------------------------------------------------
    # Internal writer
    # ------------------------------------------------------------------

    def _write(self, payload: dict) -> None:
        """Attach ``timestamp``, serialise to JSON, append to file, and log.

        Args:
            payload: Event dict — must already contain ``event`` and
                     ``run_id`` keys before this method is called.
        """
        # Attach timestamp to every event
        payload["timestamp"] = datetime.now(tz=timezone.utc).isoformat()

        line = json.dumps(payload, default=str)

        # Append to metrics.jsonl — fail-safe: log warning on any I/O error
        try:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            _collector_logger.warning(
                "Failed to write metrics event to %s: %s",
                self._path,
                exc,
                extra={"event": payload.get("event"), "run_id": payload.get("run_id")},
            )

        # Always log regardless of file write outcome
        _collector_logger.info(
            payload.get("event", "metrics_event"),
            extra={k: v for k, v in payload.items() if k != "timestamp"},
        )


# ---------------------------------------------------------------------------
# Step 5 — Module-level singleton
# ---------------------------------------------------------------------------

#: Shared ``MetricsCollector`` instance.  Import directly::
#:
#:     from core.observability import metrics
metrics: MetricsCollector = MetricsCollector()
