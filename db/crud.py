# evalci/db/crud.py
# All database read/write operations for EvalCI.

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    CategoryScore,
    EvalRun,
    FingerprintReport,
    QuestionScore,
    RunStatus,
)


# ---------------------------------------------------------------------------
# EvalRun CRUD
# ---------------------------------------------------------------------------


async def create_run(
    db: AsyncSession,
    commit_sha: str,
    branch: str,
    triggered_by: str = "github-actions",
) -> EvalRun:
    """Insert a new EvalRun record in PENDING status."""
    run = EvalRun(
        commit_sha=commit_sha,
        branch=branch,
        triggered_by=triggered_by,
        status=RunStatus.PENDING,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def update_run_status(
    db: AsyncSession,
    run_id: str,
    status: RunStatus | None,
    overall_score: float | None = None,
    passed: bool | None = None,
    error_message: str | None = None,
    celery_task_id: str | None = None,
) -> EvalRun:
    """Update the status (and optionally score/pass fields) of an existing run."""
    result = await db.execute(select(EvalRun).where(EvalRun.id == uuid.UUID(str(run_id))))
    run = result.scalar_one_or_none()
    if run is None:
        raise ValueError(f"EvalRun {run_id} not found")

    if status is not None:
        run.status = status
    if overall_score is not None:
        run.overall_score = overall_score
    if passed is not None:
        run.passed = passed
    if error_message is not None:
        run.error_message = error_message
    if celery_task_id is not None:
        run.celery_task_id = celery_task_id
    if status in (RunStatus.COMPLETE, RunStatus.FAILED):
        run.completed_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(run)
    return run


async def get_run_by_id(db: AsyncSession, run_id: str) -> EvalRun | None:
    """Fetch a single EvalRun by UUID."""
    result = await db.execute(select(EvalRun).where(EvalRun.id == uuid.UUID(str(run_id))))
    return result.scalar_one_or_none()


async def get_all_runs(
    db: AsyncSession,
    limit: int = 50,
    offset: int = 0,
    branch: str | None = None,
) -> list[EvalRun]:
    """Return paginated runs ordered by created_at descending."""
    stmt = select(EvalRun).order_by(desc(EvalRun.created_at)).limit(limit).offset(offset)
    if branch:
        stmt = stmt.where(EvalRun.branch == branch)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_last_successful_run_on_branch(
    db: AsyncSession,
    branch: str = "main",
) -> EvalRun | None:
    """Find the most recent COMPLETE run on the given branch."""
    result = await db.execute(
        select(EvalRun)
        .where(EvalRun.branch == branch, EvalRun.status == RunStatus.COMPLETE)
        .order_by(desc(EvalRun.completed_at))
        .limit(1)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# QuestionScore CRUD
# ---------------------------------------------------------------------------


async def bulk_insert_question_scores(
    db: AsyncSession,
    run_id: str,
    scores: list[dict],
) -> None:
    """Bulk-insert per-question scores for a completed run."""
    objs = [
        QuestionScore(
            run_id=uuid.UUID(str(run_id)),
            question_id=s["question_id"],
            category=s["category"],
            question_text=s["question"],
            answer=s["answer"],
            contexts=s["contexts"],
            ground_truth=s["ground_truth"],
            correctness=s["correctness"],
            grounding=s["grounding"],
            hallucination_risk=s["hallucination_risk"],
            context_recall=s["context_recall"],
            context_precision=s["context_precision"],
            from_cache=s.get("from_cache", False),
        )
        for s in scores
    ]
    db.add_all(objs)
    await db.commit()


# ---------------------------------------------------------------------------
# CategoryScore CRUD
# ---------------------------------------------------------------------------


async def bulk_insert_category_scores(
    db: AsyncSession,
    run_id: str,
    category_scores: dict[str, dict],
) -> None:
    """Bulk-insert aggregated category scores."""
    objs = [
        CategoryScore(
            run_id=uuid.UUID(str(run_id)),
            category=cat,
            question_count=data["question_count"],
            avg_correctness=data["avg_correctness"],
            avg_grounding=data["avg_grounding"],
            avg_hallucination_risk=data["avg_hallucination_risk"],
            avg_context_recall=data["avg_context_recall"],
            avg_context_precision=data["avg_context_precision"],
            passed=data["passed"],
        )
        for cat, data in category_scores.items()
    ]
    db.add_all(objs)
    await db.commit()


async def get_category_scores_for_run(
    db: AsyncSession,
    run_id: str,
) -> dict[str, dict]:
    """Return {category: {metric: value}} for a completed run."""
    result = await db.execute(
        select(CategoryScore).where(CategoryScore.run_id == uuid.UUID(str(run_id)))
    )
    rows = result.scalars().all()
    return {
        row.category: {
            "avg_correctness": row.avg_correctness,
            "avg_grounding": row.avg_grounding,
            "avg_hallucination_risk": row.avg_hallucination_risk,
            "avg_context_recall": row.avg_context_recall,
            "avg_context_precision": row.avg_context_precision,
            "question_count": row.question_count,
            "passed": row.passed,
        }
        for row in rows
    }


# ---------------------------------------------------------------------------
# FingerprintReport CRUD
# ---------------------------------------------------------------------------


async def save_fingerprint(
    db: AsyncSession,
    run_id: str,
    baseline_run_id: str | None,
    report: dict,
) -> FingerprintReport:
    """Validate and persist the exact contract returned by HTTP and read by the gate."""
    from core.reports import FingerprintReport as Report
    validated = Report.model_validate(report)
    if str(validated.run_id) != str(run_id) or (str(validated.baseline_run_id) if validated.baseline_run_id else None) != baseline_run_id:
        raise ValueError("Report identity does not match persistence arguments")
    fp = await get_fingerprint_by_run(db, run_id)
    if fp is None:
        fp = FingerprintReport(run_id=uuid.UUID(str(run_id)))
        db.add(fp)
    fp.baseline_run_id = validated.baseline_run_id
    fp.overall_regressed = validated.overall_regressed
    fp.top_failing_component = validated.top_failing_component
    fp.summary = validated.summary
    fp.regressions = [r.model_dump(mode="json") for r in validated.regressions]
    fp.action_items = validated.action_items
    fp.report = validated.model_dump(mode="json")
    await db.commit()
    await db.refresh(fp)
    return fp


async def get_fingerprint_by_run(
    db: AsyncSession,
    run_id: str,
) -> FingerprintReport | None:
    """Retrieve the FingerprintReport for a specific run."""
    result = await db.execute(
        select(FingerprintReport).where(FingerprintReport.run_id == uuid.UUID(str(run_id)))
    )
    return result.scalar_one_or_none()
