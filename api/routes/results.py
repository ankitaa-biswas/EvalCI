# evalci/api/routes/results.py
# GET /results — fetch run history, per-run details, and fingerprint reports.

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.run import EvalRunDetail, EvalRunSummary
from core.reports import FingerprintReport
from db.crud import get_all_runs, get_fingerprint_by_run, get_run_by_id
from db.database import get_db
from db.models import RunStatus

router = APIRouter()


@router.get(
    "/",
    response_model=list[EvalRunSummary],
    summary="List all historical evaluation runs",
)
async def list_runs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    branch: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Return paginated list of past evaluation runs."""
    runs = await get_all_runs(db, limit=limit, offset=offset, branch=branch)
    return [
        EvalRunSummary(
            run_id=r.id,
            commit_sha=r.commit_sha,
            branch=r.branch,
            triggered_by=r.triggered_by,
            status=r.status,
            overall_score=r.overall_score,
            passed=r.passed,
            created_at=r.created_at,
            completed_at=r.completed_at,
            has_fingerprint=r.fingerprint is not None,
        )
        for r in runs
    ]


@router.get(
    "/{run_id}",
    response_model=EvalRunDetail,
    summary="Fetch full results for a specific evaluation run",
)
async def get_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return full result payload for a single run."""
    run = await get_run_by_id(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if run.status == RunStatus.PENDING:
        raise HTTPException(status_code=425, detail="Run has not started yet")

    per_question = [
        {
            "question_id": s.question_id,
            "category": s.category,
            "question": s.question_text,
            "answer": s.answer,
            "contexts": s.contexts,
            "ground_truth": s.ground_truth,
            "correctness": s.correctness,
            "grounding": s.grounding,
            "hallucination_risk": s.hallucination_risk,
            "context_recall": s.context_recall,
            "context_precision": s.context_precision,
            "from_cache": s.from_cache,
        }
        for s in (run.scores or [])
    ]

    category_scores = {
        c.category: {
            "question_count": c.question_count,
            "avg_correctness": c.avg_correctness,
            "avg_grounding": c.avg_grounding,
            "avg_hallucination_risk": c.avg_hallucination_risk,
            "avg_context_recall": c.avg_context_recall,
            "avg_context_precision": c.avg_context_precision,
            "passed": c.passed,
        }
        for c in (run.categories or [])
    }

    return EvalRunDetail(
        run_id=run.id,
        commit_sha=run.commit_sha,
        branch=run.branch,
        triggered_by=run.triggered_by,
        status=run.status,
        overall_score=run.overall_score,
        passed=run.passed,
        created_at=run.created_at,
        completed_at=run.completed_at,
        has_fingerprint=run.fingerprint is not None,
        per_question_scores=per_question,
        category_scores=category_scores,
        fingerprint_available=run.fingerprint is not None,
        baseline_run_id=run.baseline_run_id,
        completion=run.completion,
        provenance=run.provenance,
        evaluation_errors=run.evaluation_errors or [],
        error_message=run.error_message,
    )


@router.get(
    "/{run_id}/fingerprint",
    response_model=FingerprintReport,
    summary="Fetch the Regression Fingerprint Report for a run",
)
async def get_fingerprint(
    run_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return the Regression Fingerprint Report for a completed run."""
    run = await get_run_by_id(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if run.status == RunStatus.FAILED:
        raise HTTPException(status_code=409, detail="Evaluation failed; a regression comparison cannot pass. Inspect run errors.")
    if run.status != RunStatus.COMPLETE:
        raise HTTPException(status_code=425, detail="Run not yet completed")

    fp = await get_fingerprint_by_run(db, run_id)
    if fp is None:
        raise HTTPException(status_code=409, detail="No validated comparison report is available. Inspect run status and errors; this cannot pass the quality gate.")
    try:
        report = FingerprintReport.model_validate(fp.report)
        if str(report.run_id) != run_id or report.completion.model_dump() != run.completion or report.provenance.model_dump(mode="json") != run.provenance:
            raise ValueError("Report identity, completion or provenance differs from the run")
        if report.baseline_run_id != run.baseline_run_id or run.passed != report.gate_passed:
            raise ValueError("Report baseline or gate decision differs from the run")
        return report
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=f"Stored evaluation report is invalid: {exc}") from exc
