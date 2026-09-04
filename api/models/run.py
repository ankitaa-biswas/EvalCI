# evalci/api/models/run.py
# Purpose: Pydantic request/response models for evaluation runs.
# These models validate API input and serialise API output; they are distinct
# from the SQLAlchemy ORM models in db/models.py.

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


class RunStatus(str, Enum):
    """Possible lifecycle states of an evaluation run."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class EvalRunRequest(BaseModel):
    """
    Payload sent by the CI trigger (GitHub Actions) when starting a new
    evaluation run.

    Attributes:
        commit_sha: The full 40-character Git commit hash being evaluated.
        branch: Source branch name (e.g. ``main``, ``feat/new-retriever``).
        triggered_by: Username or service account that initiated the run.
        question_set_path: Optional override path to a custom question JSON
            file; defaults to ``tests/test_suite.json``.
        baseline_run_id: Optional UUID of a previous run to compare against
            for regression fingerprinting.  If omitted, the system will use
            the last successful run on ``main``.
        rag_endpoint: Optional override URL of the RAG system to query.
            Useful for evaluating a feature-branch deployment.
    """

    commit_sha: str = Field(..., min_length=40, max_length=40, pattern=r"^[0-9a-f]{40}$")
    branch: str = Field(..., min_length=1, max_length=255)
    triggered_by: str = Field(default="github-actions")
    question_set_path: Optional[str] = Field(default=None)
    baseline_run_id: Optional[UUID] = Field(default=None)
    rag_endpoint: Optional[HttpUrl] = Field(default=None)

    model_config = {"json_schema_extra": {"examples": [{
        "commit_sha": "a" * 40,
        "branch": "feat/new-retriever",
        "triggered_by": "github-actions",
        "baseline_run_id": None,
        "rag_endpoint": None,
    }]}}


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class EvalRunResponse(BaseModel):
    """
    Returned immediately after a run is accepted and enqueued.

    Attributes:
        run_id: UUID assigned to this evaluation run.
        status: Always ``PENDING`` at this stage.
        queue_position: Estimated position in the Celery task queue.
        stream_url: URL to subscribe to live SSE updates.
    """

    run_id: UUID
    status: RunStatus = RunStatus.PENDING
    queue_position: int
    stream_url: str


class EvalRunSummary(BaseModel):
    """
    Lightweight summary of a completed (or in-progress) evaluation run
    returned by the list endpoint.
    """

    run_id: UUID
    commit_sha: str
    branch: str
    triggered_by: str
    status: RunStatus
    overall_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    passed: Optional[bool] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    has_fingerprint: bool = False


class EvalRunDetail(EvalRunSummary):
    """
    Full result payload for a single completed evaluation run.

    Extends ``EvalRunSummary`` with per-question scores and category
    aggregates. This is the model returned by ``GET /results/{run_id}``.

    Attributes:
        per_question_scores: List of individual question score objects.
        category_scores: Aggregated scores grouped by question category.
        baseline_run_id: The run this was compared against, if any.
        fingerprint_available: Whether a Regression Fingerprint exists.
    """

    per_question_scores: list[dict] = Field(default_factory=list)
    category_scores: dict = Field(default_factory=dict)
    baseline_run_id: Optional[UUID] = None
    fingerprint_available: bool = False
