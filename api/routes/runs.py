"""
api/routes/runs.py
──────────────────
FastAPI router for evaluation run lifecycle endpoints.

Endpoints
─────────
    POST   /runs                  Trigger a new evaluation run.
    GET    /runs/{run_id}         Fetch a single run (with optional fingerprint).
    GET    /runs                  List all runs, newest first, with optional branch filter.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import FingerprintResponse, RunResponse, StartRunRequest
from db.database import get_db
from db.models import EvalRun, Fingerprint

logger = logging.getLogger(__name__)

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


def _build_run_response(run: EvalRun, fingerprint: Fingerprint | None) -> RunResponse:
    """Assemble a ``RunResponse`` from ORM objects.

    Args:
        run:         The ``EvalRun`` ORM instance.
        fingerprint: The associated ``Fingerprint`` row, or ``None``.

    Returns:
        A fully populated ``RunResponse``.
    """
    fp_response: Optional[FingerprintResponse] = None
    if fingerprint is not None:
        fp_response = FingerprintResponse.from_orm_row(fingerprint)

    return RunResponse(
        id=run.id,
        commit_sha=run.commit_sha,
        branch=run.branch,
        created_at=run.created_at,
        status=run.status,
        overall_severity=run.overall_severity,
        fingerprint=fp_response,
    )


async def _get_fingerprint_for_run(
    run_id: uuid.UUID,
    db: AsyncSession,
) -> Fingerprint | None:
    """Fetch the ``Fingerprint`` row for a given run, returning ``None`` if absent.

    Args:
        run_id: UUID of the evaluation run.
        db:     Active async database session.

    Returns:
        ``Fingerprint`` ORM instance or ``None``.
    """
    result = await db.execute(
        select(Fingerprint).where(Fingerprint.run_id == run_id)
    )
    return result.scalar_one_or_none()


# ─────────────────────────────────────────────────────────────────────────────
# POST /runs — Trigger a new evaluation run
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=RunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a new evaluation run",
    description=(
        "Creates a new ``EvalRun`` record with status ``pending`` and dispatches "
        "a Celery background task to perform the evaluation asynchronously. "
        "Poll ``GET /runs/{run_id}`` to check progress."
    ),
)
async def create_run(
    body: StartRunRequest,
    db: AsyncSession = Depends(get_db),
) -> RunResponse:
    """Create a new evaluation run and dispatch it to the Celery worker pool.

    Args:
        body: Validated ``StartRunRequest`` payload.
        db:   Injected async database session.

    Returns:
        ``RunResponse`` for the newly created run (status will be ``"pending"``).

    Raises:
        HTTPException 500: If the database insert or Celery dispatch fails.
    """
    # Create the EvalRun record
    try:
        run = EvalRun(
            id=uuid.uuid4(),
            commit_sha=body.commit_sha,
            branch=body.branch,
            status="pending",
            overall_severity=None,
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
    except Exception as exc:
        await db.rollback()
        logger.exception("Failed to create EvalRun record: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create evaluation run in the database.",
        ) from exc

    # Dispatch to the Celery worker — import lazily to avoid circular imports
    # at module load time and to allow the DB commit to succeed even if the
    # broker is temporarily unavailable.
    try:
        from core.queue import run_evaluation_task  # noqa: PLC0415

        run_evaluation_task.delay(
            run_id=str(run.id),
            commit_sha=body.commit_sha,
            rag_endpoint=str(body.rag_endpoint),
            baseline_run_id=(
                str(body.baseline_run_id) if body.baseline_run_id else None
            ),
        )
        logger.info(
            "Dispatched evaluation task for run_id=%s commit=%s",
            run.id,
            body.commit_sha,
        )
    except Exception as exc:
        # The DB record was already committed; mark it failed so it's not orphaned.
        logger.exception(
            "Failed to dispatch Celery task for run_id=%s: %s", run.id, exc
        )
        try:
            run.status = "failed"
            await db.commit()
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Evaluation run was created but could not be dispatched to the worker.",
        ) from exc

    return _build_run_response(run, fingerprint=None)


# ─────────────────────────────────────────────────────────────────────────────
# GET /runs/{run_id} — Fetch a single run
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/{run_id}",
    response_model=RunResponse,
    summary="Get a single evaluation run",
    description=(
        "Returns the full ``RunResponse`` for the given run UUID, including a "
        "nested ``FingerprintResponse`` if regression fingerprinting has completed."
    ),
)
async def get_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> RunResponse:
    """Fetch a single evaluation run by its UUID.

    Args:
        run_id: UUID path parameter.
        db:     Injected async database session.

    Returns:
        ``RunResponse`` with nested fingerprint if available.

    Raises:
        HTTPException 404: If no run with the given UUID exists.
        HTTPException 500: On unexpected database errors.
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

    try:
        fingerprint = await _get_fingerprint_for_run(run_id, db)
    except Exception as exc:
        logger.exception("DB error fetching fingerprint for run %s: %s", run_id, exc)
        fingerprint = None  # degrade gracefully — return run without fingerprint

    return _build_run_response(run, fingerprint)


# ─────────────────────────────────────────────────────────────────────────────
# GET /runs — List all runs
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=list[RunResponse],
    summary="List all evaluation runs",
    description=(
        "Returns all evaluation runs ordered by ``created_at`` descending "
        "(newest first). Optionally filter by ``branch``."
    ),
)
async def list_runs(
    branch: Optional[str] = Query(
        default=None,
        description="Filter runs by exact branch name.",
        examples=["main"],
    ),
    db: AsyncSession = Depends(get_db),
) -> list[RunResponse]:
    """List all evaluation runs, newest first, with optional branch filter.

    Args:
        branch: Optional branch name to filter by.
        db:     Injected async database session.

    Returns:
        List of ``RunResponse`` objects (fingerprints are not nested here for
        performance; use ``GET /runs/{run_id}`` to get a run with its fingerprint).

    Raises:
        HTTPException 500: On unexpected database errors.
    """
    try:
        query = select(EvalRun).order_by(desc(EvalRun.created_at))
        if branch:
            query = query.where(EvalRun.branch == branch)
        result = await db.execute(query)
        runs = result.scalars().all()
    except Exception as exc:
        logger.exception("DB error listing runs: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error while listing runs.",
        ) from exc

    # Fingerprints are not fetched in the list view for efficiency
    return [_build_run_response(run, fingerprint=None) for run in runs]
