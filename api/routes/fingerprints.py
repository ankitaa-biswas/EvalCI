"""
api/routes/fingerprints.py
──────────────────────────
FastAPI router for regression fingerprint endpoints.

Endpoints
─────────
    GET /fingerprints/{run_id}                        Fetch fingerprint for a run.
    GET /fingerprints/{run_id}/compare/{baseline_run_id}  Compare two fingerprints.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import FingerprintResponse
from db.database import get_db
from db.models import EvalRun, Fingerprint

logger = logging.getLogger(__name__)

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


async def _fetch_run_or_404(run_id: uuid.UUID, db: AsyncSession) -> EvalRun:
    """Fetch an ``EvalRun`` by UUID, raising 404 if not found.

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


async def _fetch_fingerprint_or_404(run_id: uuid.UUID, db: AsyncSession) -> Fingerprint:
    """Fetch a ``Fingerprint`` for a run UUID, raising 404 if absent.

    Args:
        run_id: UUID of the evaluation run.
        db:     Active async database session.

    Returns:
        ``Fingerprint`` ORM instance.

    Raises:
        HTTPException 404: If no fingerprint exists for the run.
        HTTPException 500: On unexpected DB errors.
    """
    try:
        result = await db.execute(
            select(Fingerprint).where(Fingerprint.run_id == run_id)
        )
        fp = result.scalar_one_or_none()
    except Exception as exc:
        logger.exception("DB error fetching fingerprint for run %s: %s", run_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error while fetching fingerprint.",
        ) from exc

    if fp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No fingerprint found for run '{run_id}'. "
                   "The run may still be in progress or may not have completed successfully.",
        )
    return fp


# ─────────────────────────────────────────────────────────────────────────────
# GET /fingerprints/{run_id}
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/{run_id}",
    response_model=FingerprintResponse,
    summary="Get regression fingerprint for a run",
    description=(
        "Returns the ``FingerprintResponse`` for the given run, including the "
        "attribution breakdown across retriever, generator, prompt, and kb components. "
        "Returns 404 if the run does not exist or fingerprinting has not completed."
    ),
)
async def get_fingerprint(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> FingerprintResponse:
    """Fetch the regression fingerprint for a completed evaluation run.

    Args:
        run_id: UUID path parameter identifying the evaluation run.
        db:     Injected async database session.

    Returns:
        ``FingerprintResponse`` with attribution dict and severity.

    Raises:
        HTTPException 404: If the run or its fingerprint does not exist.
        HTTPException 500: On unexpected database errors.
    """
    # Confirm the run exists before reporting on its fingerprint
    await _fetch_run_or_404(run_id, db)
    fp = await _fetch_fingerprint_or_404(run_id, db)
    return FingerprintResponse.from_orm_row(fp)


# ─────────────────────────────────────────────────────────────────────────────
# GET /fingerprints/{run_id}/compare/{baseline_run_id}
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/{run_id}/compare/{baseline_run_id}",
    summary="Compare two regression fingerprints",
    description=(
        "Computes the delta between the fingerprint of ``run_id`` (current) and "
        "``baseline_run_id`` (baseline). Returns a dict with keys ``current``, "
        "``baseline``, and ``delta`` — the signed difference in severity and each "
        "attribution weight. Positive delta means the current run is worse."
    ),
)
async def compare_fingerprints(
    run_id: uuid.UUID,
    baseline_run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Compare the regression fingerprint of two evaluation runs.

    Computes signed deltas (current − baseline) for:
    - ``severity``
    - Each attribution weight (``retriever``, ``generator``, ``prompt``, ``kb``)

    Args:
        run_id:          UUID of the current (newer) evaluation run.
        baseline_run_id: UUID of the baseline (reference) evaluation run.
        db:              Injected async database session.

    Returns:
        Dict with keys:
        - ``current``  (``FingerprintResponse``) — the current run's fingerprint.
        - ``baseline`` (``FingerprintResponse``) — the baseline run's fingerprint.
        - ``delta``    (dict)                    — signed differences (current − baseline).

    Raises:
        HTTPException 404: If either run or its fingerprint does not exist.
        HTTPException 500: On unexpected database errors.
    """
    # Validate both runs and their fingerprints exist
    await _fetch_run_or_404(run_id, db)
    await _fetch_run_or_404(baseline_run_id, db)

    current_fp  = await _fetch_fingerprint_or_404(run_id, db)
    baseline_fp = await _fetch_fingerprint_or_404(baseline_run_id, db)

    current_response  = FingerprintResponse.from_orm_row(current_fp)
    baseline_response = FingerprintResponse.from_orm_row(baseline_fp)

    # Compute signed deltas: positive = current is worse
    attribution_delta: dict[str, float] = {
        component: round(
            current_response.attribution.get(component, 0.0)
            - baseline_response.attribution.get(component, 0.0),
            4,
        )
        for component in ("retriever", "generator", "prompt", "kb")
    }

    delta = {
        "severity":    round(current_response.severity - baseline_response.severity, 4),
        "attribution": attribution_delta,
        "dominant_failure_changed": (
            current_response.dominant_failure != baseline_response.dominant_failure
        ),
    }

    return {
        "current":  current_response.model_dump(),
        "baseline": baseline_response.model_dump(),
        "delta":    delta,
    }
