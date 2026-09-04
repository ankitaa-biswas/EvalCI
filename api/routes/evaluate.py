# evalci/api/routes/evaluate.py
# POST /evaluate — trigger a new evaluation run.

import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.run import EvalRunRequest, EvalRunResponse, RunStatus
from core.queue import dispatch_eval_task
from db.crud import create_run, update_run_status, get_run_by_id
from db.database import get_db
from db.models import RunStatus as DBRunStatus

router = APIRouter()

# Resolve the question set path relative to the project root
_DEFAULT_QUESTION_SET = os.path.join(
    os.path.dirname(__file__), "..", "..", "tests", "test_suite.json"
)


@router.post(
    "/",
    response_model=EvalRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a new evaluation run",
)
async def trigger_evaluation(
    payload: EvalRunRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Accept an evaluation request from CI, create a DB run record,
    enqueue a Celery task, and return the run_id immediately.
    """
    # 1. Create DB record in PENDING state
    run = await create_run(
        db,
        commit_sha=payload.commit_sha,
        branch=payload.branch,
        triggered_by=payload.triggered_by,
    )
    run_id = str(run.id)

    # 2. Resolve question set path
    question_set_path = str(payload.question_set_path or _DEFAULT_QUESTION_SET)

    # 3. Enqueue Celery task
    try:
        celery_task_id = dispatch_eval_task(
            run_id=run_id,
            commit_sha=payload.commit_sha,
            branch=payload.branch,
            question_set_path=question_set_path,
            baseline_run_id=str(payload.baseline_run_id) if payload.baseline_run_id else None,
            rag_endpoint=str(payload.rag_endpoint) if payload.rag_endpoint else None,
        )
        # Store celery task id
        await update_run_status(
            db, run_id, DBRunStatus.PENDING, celery_task_id=celery_task_id
        )
    except Exception as exc:
        await update_run_status(
            db, run_id, DBRunStatus.FAILED, error_message=str(exc)
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to enqueue evaluation task: {exc}",
        )

    return EvalRunResponse(
        run_id=run.id,
        status=RunStatus.PENDING,
        queue_position=1,
        stream_url=f"/stream/{run_id}",
    )


@router.get(
    "/{run_id}/status",
    summary="Poll the status of an in-flight evaluation run",
)
async def get_run_status(
    run_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return just the current status string without full score payload."""
    run = await get_run_by_id(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    progress_pct = None
    if run.status == DBRunStatus.COMPLETE:
        progress_pct = 100.0
    elif run.status == DBRunStatus.RUNNING:
        progress_pct = 50.0  # Actual progress is tracked via SSE events

    return {
        "run_id": run_id,
        "status": run.status.value,
        "overall_score": run.overall_score,
        "passed": run.passed,
        "progress_pct": progress_pct,
        "error_message": run.error_message,
    }
