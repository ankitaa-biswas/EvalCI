"""
api/schemas.py
──────────────
Pydantic v2 request and response schemas for EvalCI's REST API.

All response schemas use ``model_config = ConfigDict(from_attributes=True)``
so SQLAlchemy ORM objects can be passed directly to FastAPI response serialization
without manually mapping fields.

Schema hierarchy:
    Request
        StartRunRequest         POST /runs
    Response
        FingerprintResponse     nested inside RunResponse + returned by /fingerprints
        RunResponse             /runs, /runs/{run_id}
        ClusterResponse         /clusters/{run_id}
        HeatmapResponse         /clusters/{run_id}/heatmap
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field


# ─────────────────────────────────────────────────────────────────────────────
# Request schemas
# ─────────────────────────────────────────────────────────────────────────────


# Canonical request/report aliases retained for legacy imports.
from api.models.run import EvalRunRequest as StartRunRequest
from core.reports import FingerprintReport as FingerprintResponse


class RunResponse(BaseModel):
    """Serialized form of an ``EvalRun`` ORM row, with optional nested fingerprint.

    Attributes:
        id:               Primary key UUID.
        commit_sha:       Git commit SHA that triggered this run.
        branch:           Git branch name.
        created_at:       UTC timestamp when the run was created.
        status:           Current run lifecycle status.
        overall_severity: Aggregated severity score (0–10), ``None`` while pending/running.
        fingerprint:      Nested ``FingerprintResponse`` if a fingerprint has been
                          computed for this run; ``None`` otherwise.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    commit_sha: str
    branch: str
    created_at: datetime
    status: str
    overall_severity: Optional[float] = None
    fingerprint: Optional[FingerprintResponse] = None


class ClusterResponse(BaseModel):
    """Serialized form of a ``FailureCluster`` ORM row.

    Attributes:
        id:               Primary key UUID.
        run_id:           The evaluation run this cluster belongs to.
        cluster_label:    Human-readable label for the cluster (e.g. "Billing Questions").
        question_ids:     List of question IDs grouped into this cluster.
        dominant_failure: The component most attributed to this cluster's failures.
        size:             Number of questions in the cluster.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    cluster_label: str
    question_ids: list[str]
    dominant_failure: str
    size: int


class HeatmapResponse(BaseModel):
    """Aggregated metric scores structured for heatmap visualisation.

    The ``scores`` field is a two-level nested dict:
    ``{category → {metric → float}}``
    so the dashboard can render a category × metric matrix directly.

    Attributes:
        categories: Ordered list of category labels (rows of the heatmap).
        metrics:    Ordered list of metric names (columns of the heatmap).
        scores:     Nested mapping of category → metric → average score (0–1).
    """

    model_config = ConfigDict(from_attributes=True)

    categories: list[str] = Field(
        description="Ordered list of question category labels."
    )
    metrics: list[str] = Field(
        description="Ordered list of metric column names.",
        examples=[["context_recall", "context_precision", "groundedness", "correctness"]],
    )
    scores: dict[str, dict[str, float]] = Field(
        description=(
            "Two-level mapping: category → metric → average score. "
            "All score values are floats in [0.0, 1.0]."
        )
    )
