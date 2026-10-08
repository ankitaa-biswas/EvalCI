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
    import statistics as _statistics

    from core.cache import get_redis_client, publish_score_event
    from core.evaluator import RAGEvaluator, aggregate_by_category
    from core.fingerprinter import RegressionFingerprinter
    from db.crud import (
        bulk_insert_category_scores,
        bulk_insert_question_scores,
        get_category_scores_for_run,
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

            # --- 4. Run RAG evaluation (synchronous; offloaded to thread pool) ---
            if not rag_endpoint:
                raise ValueError("rag_endpoint is required but was not provided.")

            evaluator = RAGEvaluator()
            loop = asyncio.get_event_loop()
            # RAGEvaluator.evaluate() is synchronous (uses httpx.Client).
            # Run it in a thread pool so we do not block the event loop.
            raw_results: list[dict] = await loop.run_in_executor(
                None, evaluator.evaluate, questions, rag_endpoint
            )

            # RAGEvaluator returns nested {"scores": {...}} dicts.  The rest of
            # the pipeline (aggregate_by_category, bulk_insert_question_scores)
            # expects the flat format produced by score_to_dict.  Build a
            # parallel mapping from question_id → raw rag_output so we can call
            # score_to_dict to produce the flat dicts.
            #
            # RAGEvaluator.evaluate() already combines q/rag_out/ragas_row
            # into the nested result dict, so we reconstruct the flat form here.
            scores: list[dict] = []
            for item in raw_results:
                s = item["scores"]
                flat = {
                    "question_id":       item["question_id"],
                    "category":          item["category"],
                    # question text is not carried through RAGEvaluator.evaluate();
                    # look it up from the original questions list.
                    "question":          next(
                        (q["question"] for q in questions if q["id"] == item["question_id"]),
                        "",
                    ),
                    "answer":            item["answer"],
                    "contexts":          item["retrieved_chunks"],
                    "ground_truth":      next(
                        (q["ground_truth"] for q in questions if q["id"] == item["question_id"]),
                        "",
                    ),
                    "correctness":       s["correctness"],
                    "grounding":         s["groundedness"],
                    "hallucination_risk": round(max(0.0, 1.0 - s["groundedness"]), 4),
                    "context_recall":    s["context_recall"],
                    "context_precision": s["context_precision"],
                    "from_cache":        False,
                }
                scores.append(flat)

            # --- 5. Aggregate by category ---
            cat_scores = aggregate_by_category(scores, category_thresholds)

            # --- 6. Persist scores ---
            await bulk_insert_question_scores(db, run_id, scores)
            await bulk_insert_category_scores(db, run_id, cat_scores)

            # --- 7. Compute overall score ---
            if scores:
                overall = _statistics.mean(s["correctness"] for s in scores)
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
                    # Fetch per-category metric averages for both runs from the DB.
                    current_cat_data = await get_category_scores_for_run(db, run_id)
                    baseline_cat_data = await get_category_scores_for_run(
                        db, resolved_baseline_id
                    )

                    # Build scalar metric snapshots (mean across all categories).
                    def _mean_snapshot(cat_data: dict) -> dict:
                        """Average per-category metrics into one scalar snapshot."""
                        rows = list(cat_data.values())
                        if not rows:
                            return {
                                "correctness": 0.0,
                                "groundedness": 0.0,
                                "context_recall": 0.0,
                                "context_precision": 0.0,
                            }
                        return {
                            "correctness":       _statistics.mean(r["avg_correctness"]      for r in rows),
                            "groundedness":      _statistics.mean(r["avg_grounding"]         for r in rows),
                            "context_recall":    _statistics.mean(r["avg_context_recall"]   for r in rows),
                            "context_precision": _statistics.mean(r["avg_context_precision"] for r in rows),
                        }

                    current_snapshot  = _mean_snapshot(current_cat_data)
                    baseline_snapshot = _mean_snapshot(baseline_cat_data)

                    fp = RegressionFingerprinter()
                    result = fp.compute(baseline_snapshot, current_snapshot)

                    # Determine whether there is an overall regression (any metric
                    # dropped beyond the noise floor of −0.05).
                    overall_regressed = any(d < -0.05 for d in result["deltas"].values())
                    dominant = result["dominant_failure"]
                    severity = result["severity"]

                    # Build action items from the attribution breakdown.
                    action_items: list[str] = []
                    attr = result["attribution"]
                    if attr.get("retriever", 0) > 0.25:
                        action_items.append("Investigate retriever: context recall and/or precision dropped.")
                    if attr.get("generator", 0) > 0.25:
                        action_items.append("Investigate generator: groundedness dropped with stable retrieval.")
                    if attr.get("prompt", 0) > 0.25:
                        action_items.append("Investigate prompt template: correctness regressed without other signals.")
                    if attr.get("kb", 0) > 0.25:
                        action_items.append("Investigate knowledge base: diffuse quality decline detected.")

                    summary = (
                        f"Regression detected. Dominant failure component: {dominant}. "
                        f"Severity: {severity}/10."
                    ) if overall_regressed else "No significant regression detected."

                    report_dict = {
                        "overall_regressed":    overall_regressed,
                        "top_failing_component": dominant,
                        "summary":              summary,
                        "regressions": [
                            {
                                "metric":  metric,
                                "delta":   delta,
                                "dropped": delta < -0.05,
                            }
                            for metric, delta in result["deltas"].items()
                        ],
                        "action_items": action_items,
                    }

                    if overall_regressed:
                        await save_fingerprint(
                            db, run_id, resolved_baseline_id, report_dict
                        )
                    else:
                        logger.info(
                            f"Fingerprint computed for run {run_id}: no regression. "
                            f"Severity={severity}"
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
