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


class StartRunRequest(BaseModel):
    """Payload accepted by POST /runs to trigger a new evaluation run.

    Attributes:
        commit_sha:      The Git commit SHA being evaluated. Used as the cache
                         key so re-running the same commit returns cached scores.
        branch:          The Git branch name (e.g. "main", "feature/new-prompt").
        rag_endpoint:    Full URL of the RAG query endpoint that will be called
                         for each test question. Must be a valid HTTP/HTTPS URL.
        baseline_run_id: Optional UUID of a previous run to compare against for
                         regression fingerprinting. If omitted, the most recent
                         completed run is used as the baseline.
    """

    commit_sha: str = Field(
        ...,
        min_length=7,
        max_length=40,
        description="Git commit SHA (short or full) being evaluated.",
        examples=["a1b2c3d"],
    )
    branch: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Git branch name.",
        examples=["main"],
    )
    rag_endpoint: AnyHttpUrl = Field(
        ...,
        description="Fully-qualified HTTP/HTTPS URL of the RAG query endpoint.",
        examples=["http://localhost:8000/query"],
    )
    baseline_run_id: Optional[UUID] = Field(
        default=None,
        description=(
            "UUID of a previous completed run to use as the regression baseline. "
            "Defaults to the most recent completed run when omitted."
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Response schemas
# ─────────────────────────────────────────────────────────────────────────────


class FingerprintResponse(BaseModel):
    """Serialized form of a ``Fingerprint`` ORM row.

    The four raw ``attribution_*`` columns are collapsed into a single
    ``attribution`` dict for cleaner API responses.

    Attributes:
        id:                 Primary key UUID.
        run_id:             The evaluation run this fingerprint belongs to.
        dominant_failure:   Component label with the highest attribution weight
                            (``"retriever"`` | ``"generator"`` | ``"prompt"`` | ``"kb"``).
        severity:           Severity score 0–10 derived from correctness delta.
        confidence:         ``"high"`` | ``"medium"`` | ``"low"`` based on judge agreement.
        attribution:        Nested dict mapping component names to normalized weights.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    dominant_failure: str
    severity: float
    confidence: str
    attribution: dict[str, float] = Field(
        description=(
            "Normalized attribution weights for each failure component. "
            "Keys: retriever, generator, prompt, kb. Values sum to 1.0."
        )
    )

    @classmethod
    def from_orm_row(cls, fp: object) -> "FingerprintResponse":
        """Construct from a ``Fingerprint`` ORM object, building the attribution dict.

        Args:
            fp: A ``db.models.Fingerprint`` instance.

        Returns:
            A populated ``FingerprintResponse``.
        """
        return cls(
            id=fp.id,
            run_id=fp.run_id,
            dominant_failure=fp.dominant_failure,
            severity=fp.severity,
            confidence=fp.confidence,
            attribution={
                "retriever": fp.attribution_retriever,
                "generator": fp.attribution_generator,
                "prompt":    fp.attribution_prompt,
                "kb":        fp.attribution_kb,
            },
        )


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
