# evalci/core/queue.py
# Celery task definitions and broker configuration.

import asyncio
import json
import os

from celery import Celery
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)

# ---------------------------------------------------------------------------
# Celery application instance
# ---------------------------------------------------------------------------

celery_app = Celery(
    "evalci",
    broker=REDIS_URL,
    backend=CELERY_RESULT_BACKEND,
    include=["core.queue"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=3600,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)


# ---------------------------------------------------------------------------
# Task definitions
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="evalci.run_evaluation",
    max_retries=2,
    default_retry_delay=30,
    soft_time_limit=600,
    time_limit=660,
)
def run_evaluation_task(
    self,
    run_id: str,
    commit_sha: str,
    branch: str,
    question_set_path: str,
    baseline_run_id: str | None,
    rag_endpoint: str | None,
):
    """
    Main Celery task for running a full EvalCI evaluation.
    Uses asyncio.run() to drive the async evaluation pipeline from
    a synchronous Celery worker context.
    """
    asyncio.run(
        _async_run_evaluation(
            task=self,
            run_id=run_id,
            commit_sha=commit_sha,
            branch=branch,
            question_set_path=question_set_path,
            baseline_run_id=baseline_run_id,
            rag_endpoint=rag_endpoint,
        )
    )


async def _async_run_evaluation(
    task,
    run_id: str,
    commit_sha: str,
    branch: str,
    question_set_path: str,
    baseline_run_id: str | None,
    rag_endpoint: str | None,
) -> None:
    """Async body of the Celery evaluation task."""
    from core.cache import get_redis_client, publish_score_event
    from core.evaluator import aggregate_by_category, run_evaluation
    from core.fingerprinter import build_fingerprint
    from db.crud import (
        bulk_insert_category_scores,
        bulk_insert_question_scores,
        get_last_successful_run_on_branch,
        save_fingerprint,
        update_run_status,
    )
    from db.database import AsyncSessionLocal
    from db.models import RunStatus

    redis = get_redis_client()

    async with AsyncSessionLocal() as db:
        try:
            # --- 1. Mark run as RUNNING ---
            await update_run_status(db, run_id, RunStatus.RUNNING)

            # --- 2. Load questions ---
            with open(question_set_path, "r", encoding="utf-8") as f:
                questions = json.load(f)

            # --- 3. Load category thresholds ---
            import os as _os
            cats_path = _os.path.join(
                _os.path.dirname(question_set_path), "categories.json"
            )
            category_thresholds: dict = {}
            if _os.path.exists(cats_path):
                with open(cats_path, "r", encoding="utf-8") as f:
                    cats_data = json.load(f)
                for cat_def in cats_data.get("categories", []):
                    category_thresholds[cat_def["id"]] = cat_def.get(
                        "pass_threshold", {}
                    )

            # --- 4. Run evaluation ---
            scores = await run_evaluation(
                run_id=run_id,
                questions=questions,
                commit_sha=commit_sha,
                rag_endpoint=rag_endpoint,
            )

            # --- 5. Aggregate by category ---
            cat_scores = aggregate_by_category(scores, category_thresholds)

            # --- 6. Persist scores ---
            await bulk_insert_question_scores(db, run_id, scores)
            await bulk_insert_category_scores(db, run_id, cat_scores)

            # --- 7. Compute overall score ---
            if scores:
                import statistics
                overall = statistics.mean(s["correctness"] for s in scores)
            else:
                overall = 0.0

            pass_threshold = float(_os.getenv("PASS_THRESHOLD", "0.70"))
            passed = overall >= pass_threshold

            # --- 8. Regression fingerprint ---
            resolved_baseline_id = baseline_run_id
            if not resolved_baseline_id:
                baseline_run = await get_last_successful_run_on_branch(
                    db, branch="main"
                )
                if baseline_run and str(baseline_run.id) != run_id:
                    resolved_baseline_id = str(baseline_run.id)

            if resolved_baseline_id:
                try:
                    report = await build_fingerprint(
                        run_id=run_id,
                        baseline_run_id=resolved_baseline_id,
                        db=db,
                    )
                    if report.overall_regressed:
                        report_dict = {
                            "overall_regressed": report.overall_regressed,
                            "top_failing_component": report.top_failing_component.value,
                            "summary": report.summary,
                            "regressions": [
                                r.model_dump() for r in report.regressions
                            ],
                            "action_items": report.action_items,
                        }
                        await save_fingerprint(
                            db, run_id, resolved_baseline_id, report_dict
                        )
                except Exception as fp_err:
                    logger.warning(f"Fingerprinting failed (non-fatal): {fp_err}")

            # --- 9. Mark complete ---
            await update_run_status(
                db,
                run_id,
                RunStatus.COMPLETE,
                overall_score=round(overall, 4),
                passed=passed,
            )

            # --- 10. Publish done event ---
            await publish_score_event(
                redis,
                run_id,
                "done",
                {
                    "run_id": run_id,
                    "overall_score": round(overall, 4),
                    "passed": passed,
                    "fingerprint_available": bool(resolved_baseline_id),
                },
            )
            logger.info(f"Evaluation run {run_id} complete. Score={overall:.4f}")

        except Exception as exc:
            logger.error(f"Evaluation run {run_id} FAILED: {exc}", exc_info=True)
            try:
                await update_run_status(
                    db,
                    run_id,
                    RunStatus.FAILED,
                    error_message=str(exc),
                )
                await publish_score_event(
                    redis,
                    run_id,
                    "error",
                    {"run_id": run_id, "error": str(exc)},
                )
            except Exception:
                pass
            raise task.retry(exc=exc)


def dispatch_eval_task(
    run_id: str,
    commit_sha: str,
    branch: str,
    question_set_path: str = "tests/test_suite.json",
    baseline_run_id: str | None = None,
    rag_endpoint: str | None = None,
) -> str:
    """Enqueue the run_evaluation_task and return the Celery task ID."""
    result = run_evaluation_task.apply_async(
        kwargs={
            "run_id": run_id,
            "commit_sha": commit_sha,
            "branch": branch,
            "question_set_path": question_set_path,
            "baseline_run_id": baseline_run_id,
            "rag_endpoint": rag_endpoint,
        }
    )
    return result.id
