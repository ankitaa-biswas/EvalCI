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
    from core.configuration import load_evaluation
    from core.baselines import resolve_baseline
    from core.reports import Completion
    from core.progress import append_event, progress_payload
    question_set_path = str(payload.question_set_path or _DEFAULT_QUESTION_SET)
    try:
        questions, provenance = load_evaluation(question_set_path, payload.commit_sha, str(payload.rag_endpoint))
        if payload.baseline_run_id:
            await resolve_baseline(db, str(payload.baseline_run_id), None, provenance)
    except (ValueError, OSError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=f"Evaluation configuration error: {exc}") from exc
    run = await create_run(db, commit_sha=payload.commit_sha, branch=payload.branch,
                           triggered_by=payload.triggered_by)
    run.provenance = provenance.model_dump(mode="json")
    run.completion = Completion(expected=len(questions), attempted=0, successfully_scored=0, failed=0).model_dump()
    run.baseline_run_id = payload.baseline_run_id
    run.baseline_bootstrap = payload.baseline_bootstrap
    run.evaluation_errors = []
    await db.commit()
    run_id = str(run.id)
    await append_event(db, "progress", progress_payload(run, "queued"))

    # 3. Enqueue Celery task
    try:
        celery_task_id = dispatch_eval_task(
            run_id=run_id,
            commit_sha=payload.commit_sha,
            branch=payload.branch,
            question_set_path=question_set_path,
            baseline_run_id=str(payload.baseline_run_id) if payload.baseline_run_id else None,
            rag_endpoint=str(payload.rag_endpoint),
            baseline_bootstrap=payload.baseline_bootstrap,
        )
        # Store celery task id
        await update_run_status(
            db, run_id, None, celery_task_id=celery_task_id
        )
    except Exception as exc:
        await update_run_status(
            db, run_id, DBRunStatus.FAILED, passed=False, error_message=str(exc)
        )
        await append_event(db, "error", progress_payload(run))
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

    from sqlalchemy import select
    from db.models import RunEvent
    event = (await db.execute(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.id.desc()).limit(1))).scalar_one_or_none()
    progress = event.data if event else None
    progress_pct = round(progress["completed"] / progress["total"] * 100, 1) if progress else None

    return {
        "run_id": run_id,
        "status": run.status,
        "overall_score": run.overall_score,
        "passed": run.passed,
        "progress_pct": progress_pct,
        "completion": run.completion,
        "progress": progress,
        "error_message": run.error_message,
    }
