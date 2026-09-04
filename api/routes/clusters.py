"""
api/routes/clusters.py
──────────────────────
FastAPI router for failure cluster and heatmap endpoints.

Endpoints
─────────
    GET /clusters/{run_id}           List all failure clusters for a run.
    GET /clusters/{run_id}/heatmap   Compute and return a category × metric heatmap.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import ClusterResponse, HeatmapResponse
from db.database import get_db
from db.models import EvalRun, FailureCluster, QuestionResult

logger = logging.getLogger(__name__)

router = APIRouter()

#: The four metrics exposed in the heatmap, in display order.
_HEATMAP_METRICS: list[str] = [
    "context_recall",
    "context_precision",
    "groundedness",
    "correctness",
]


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


async def _fetch_run_or_404(run_id: uuid.UUID, db: AsyncSession) -> EvalRun:
    """Confirm a run exists, raising 404 if not.

    Args:
        run_id: UUID of the evaluation run.
        db:     Active async database session.

    Returns:
        ``EvalRun`` ORM instance.

    Raises:
        HTTPException 404: If the run does not exist.
        HTTPException 500: On unexpected DB errors.
    """
    try:
        result = await db.execute(select(EvalRun).where(EvalRun.id == run_id))
        run = result.scalar_one_or_none()
    except Exception as exc:
        logger.exception("DB error fetching run %s: %s", run_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error while fetching run.",
        ) from exc

    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evaluation run '{run_id}' not found.",
        )
    return run


# ─────────────────────────────────────────────────────────────────────────────
# GET /clusters/{run_id} — List failure clusters
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/{run_id}",
    response_model=list[ClusterResponse],
    summary="List failure clusters for a run",
    description=(
        "Returns all ``FailureCluster`` rows for the given run, ordered by "
        "``size`` descending (largest cluster first). "
        "Returns 404 if the run does not exist."
    ),
)
async def list_clusters(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[ClusterResponse]:
    """Fetch all failure clusters associated with an evaluation run.

    Clusters are computed during the evaluation pipeline and stored as rows in
    ``failure_clusters``. This endpoint simply retrieves and serialises them.

    Args:
        run_id: UUID path parameter identifying the evaluation run.
        db:     Injected async database session.

    Returns:
        List of ``ClusterResponse`` objects, largest cluster first.

    Raises:
        HTTPException 404: If the run does not exist.
        HTTPException 500: On unexpected database errors.
    """
    await _fetch_run_or_404(run_id, db)

    try:
        result = await db.execute(
            select(FailureCluster)
            .where(FailureCluster.run_id == run_id)
            .order_by(FailureCluster.size.desc())
        )
        clusters = result.scalars().all()
    except Exception as exc:
        logger.exception("DB error fetching clusters for run %s: %s", run_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error while fetching clusters.",
        ) from exc

    return [
        ClusterResponse(
            id=c.id,
            run_id=c.run_id,
            cluster_label=c.cluster_label,
            question_ids=c.question_ids,
            dominant_failure=c.dominant_failure,
            size=c.size,
        )
        for c in clusters
    ]


# ─────────────────────────────────────────────────────────────────────────────
# GET /clusters/{run_id}/heatmap — Category × metric heatmap
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/{run_id}/heatmap",
    response_model=HeatmapResponse,
    summary="Get category × metric score heatmap for a run",
    description=(
        "Aggregates all ``QuestionResult`` rows for the run, groups them by "
        "``category``, averages the four metric scores per category, and returns "
        "the result as a nested ``scores`` dict ready for heatmap visualisation. "
        "Returns 404 if the run does not exist."
    ),
)
async def get_heatmap(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HeatmapResponse:
    """Compute a category × metric average score heatmap for an evaluation run.

    Algorithm:
        1. Fetch all ``QuestionResult`` rows for ``run_id``.
        2. Group rows by ``category``.
        3. For each category, compute the arithmetic mean of each of the four
           metric columns (``context_recall``, ``context_precision``,
           ``groundedness``, ``correctness``).
        4. Assemble into ``HeatmapResponse``.

    Args:
        run_id: UUID path parameter identifying the evaluation run.
        db:     Injected async database session.

    Returns:
        ``HeatmapResponse`` with ``categories``, ``metrics``, and ``scores``.

    Raises:
        HTTPException 404: If the run does not exist.
        HTTPException 500: On unexpected database errors.
    """
    await _fetch_run_or_404(run_id, db)

    try:
        result = await db.execute(
            select(QuestionResult).where(QuestionResult.run_id == run_id)
        )
        question_results: list[QuestionResult] = list(result.scalars().all())
    except Exception as exc:
        logger.exception(
            "DB error fetching question results for run %s: %s", run_id, exc
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error while computing heatmap.",
        ) from exc

    # Group raw values by category
    # Structure: {category: {metric: [values...]}}
    grouped: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for qr in question_results:
        cat = qr.category
        grouped[cat]["context_recall"].append(qr.context_recall)
        grouped[cat]["context_precision"].append(qr.context_precision)
        grouped[cat]["groundedness"].append(qr.groundedness)
        grouped[cat]["correctness"].append(qr.correctness)

    # Compute per-category averages
    scores: dict[str, dict[str, float]] = {}
    for category, metric_values in grouped.items():
        scores[category] = {
            metric: round(
                sum(values) / len(values) if values else 0.0,
                4,
            )
            for metric, values in metric_values.items()
        }

    # Sort categories alphabetically for stable ordering in the UI
    categories = sorted(scores.keys())

    return HeatmapResponse(
        categories=categories,
        metrics=_HEATMAP_METRICS,
        scores=scores,
    )
