"""Celery evaluation task. Failures never become successful comparisons."""
import asyncio
import os
import statistics

from celery import Celery
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
celery_app = Celery("evalci", broker=REDIS_URL,
    backend=os.getenv("CELERY_RESULT_BACKEND", REDIS_URL), include=["core.queue"])
celery_app.conf.update(task_serializer="json", result_serializer="json", accept_content=["json"],
    result_expires=3600, worker_prefetch_multiplier=1, task_acks_late=True)


_SOFT_LIMIT = int(os.getenv("EVAL_TASK_SOFT_LIMIT_SECONDS", "600"))
_HARD_LIMIT = int(os.getenv("EVAL_TASK_TIME_LIMIT_SECONDS", "660"))
if not 0 < _SOFT_LIMIT < _HARD_LIMIT:
    raise ValueError("Worker time limits require 0 < soft limit < hard limit")


@celery_app.task(bind=True, name="evalci.run_evaluation", soft_time_limit=_SOFT_LIMIT, time_limit=_HARD_LIMIT)
def run_evaluation_task(self, run_id, commit_sha, branch, question_set_path,
                        baseline_run_id=None, rag_endpoint=None, baseline_bootstrap=False):
    asyncio.run(_async_run_evaluation(self, run_id, commit_sha, branch, question_set_path,
                                     baseline_run_id, rag_endpoint, baseline_bootstrap))


async def _async_run_evaluation(task, run_id, commit_sha, branch, question_set_path,
                                baseline_run_id, rag_endpoint, baseline_bootstrap=False):
    from sqlalchemy import delete
    from core.baselines import resolve_baseline
    from core.cache import get_redis_client, publish_score_event
    from core.configuration import load_evaluation
    from core.evaluator import RAGEvaluator, aggregate_by_category, category_snapshots, thresholds_pass
    from core.fingerprinter import RegressionFingerprinter
    from core.reports import Completion
    from core.progress import ProgressEvent, append_event, progress_payload
    from db.crud import (get_run_by_id, update_run_status, bulk_insert_question_scores,
        bulk_insert_category_scores, get_category_scores_for_run, save_fingerprint)
    from db.database import AsyncSessionLocal
    from db.models import RunStatus, QuestionScore, CategoryScore, FingerprintReport

    redis = get_redis_client()

    async def notify(db, kind, payload):
        row = await append_event(db, kind, payload)
        try:
            await publish_score_event(redis, run_id, kind, {**row.data, "event_id": row.id})
        except Exception:
            # PostgreSQL is the replay source. Redis publication is a wake-up
            # hint for legacy subscribers and does not decide evaluation success.
            logger.exception("Redis event publication failed for %s", run_id)

    async with AsyncSessionLocal() as db:
        run = None
        try:
            run = await get_run_by_id(db, run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")
            if run.status == RunStatus.COMPLETE:
                return  # Ignore a duplicate delivery of a completed task.
            await update_run_status(db, run_id, RunStatus.RUNNING, passed=False)
            questions, provenance = load_evaluation(question_set_path, commit_sha, rag_endpoint)
            if run.provenance != provenance.model_dump(mode="json"):
                raise ValueError("Dataset or evaluation configuration changed after dispatch; resubmit the run")
            if run.baseline_bootstrap != baseline_bootstrap or str(run.baseline_run_id or "") != str(baseline_run_id or ""):
                raise ValueError("Task baseline selection differs from the submitted run")
            if baseline_bootstrap == bool(baseline_run_id):
                raise ValueError("Choose an explicit baseline_run_id or baseline_bootstrap=true")
            run.completion = Completion(expected=len(questions), attempted=0, successfully_scored=0, failed=0).model_dump()
            run.evaluation_errors = []
            await db.commit()
            await notify(db, "progress", progress_payload(run, "starting"))
            baseline_provenance = None
            if baseline_run_id:
                _, baseline_provenance = await resolve_baseline(db, baseline_run_id, run_id, provenance)
            # Replace artifacts on task redelivery; never append duplicate questions.
            for model in (FingerprintReport, QuestionScore, CategoryScore):
                await db.execute(delete(model).where(model.run_id == run.id))
            await db.commit()
            config = provenance.configuration
            evaluator = RAGEvaluator(timeout=config["request_timeout_seconds"], configuration=config)
            loop = asyncio.get_running_loop()
            def on_progress(payload):
                # The evaluator runs in a thread. Complete each durable append
                # on this loop before the next question advances.
                event = ProgressEvent(run_id=run_id, evaluation_mode=config.get("scoring_mode", "ragas"), **payload)
                asyncio.run_coroutine_threadsafe(notify(db, "progress", event), loop).result(timeout=30)
            batch = await asyncio.to_thread(evaluator.evaluate, questions, rag_endpoint, on_progress)
            run.completion = batch.completion.model_dump()
            run.evaluation_errors = batch.errors
            await db.commit()
            # Validate coverage independently of scorer-provided counts.
            expected = {q["id"]: q["category"] for q in questions}
            actual = {s["question_id"]: s["category"] for s in batch.scores}
            if not batch.completion.complete or actual != expected or len(batch.scores) != len(questions) or batch.errors:
                raise ValueError(f"Incomplete evaluation: {batch.completion.model_dump()}; inspect evaluation_errors")
            scores = batch.scores
            categories = aggregate_by_category(scores, config["category_thresholds"])
            overall = statistics.mean(s["correctness"] for s in scores)
            global_metrics = {m: statistics.mean(s["grounding" if m == "groundedness" else m] for s in scores)
                              for m in config["metrics"]}
            quality_passed = (round(overall - config["overall_correctness_threshold"], 12) >= 0 and
                all(c["passed"] for c in categories.values()) and thresholds_pass(global_metrics, config["global_thresholds"]))
            await notify(db, "progress", progress_payload(run, "persisting"))
            await bulk_insert_question_scores(db, run_id, scores)
            await bulk_insert_category_scores(db, run_id, categories)
            baseline = await get_category_scores_for_run(db, baseline_run_id) if baseline_run_id else {}
            report = RegressionFingerprinter().report(run_id=run_id, baseline_run_id=baseline_run_id,
                completion=batch.completion, provenance=provenance, baseline_provenance=baseline_provenance,
                current=category_snapshots(categories), baseline=category_snapshots(baseline), quality_passed=quality_passed)
            await save_fingerprint(db, run_id, baseline_run_id, report.model_dump(mode="json"))
            await update_run_status(db, run_id, RunStatus.COMPLETE, overall_score=overall, passed=report.gate_passed)
            await notify(db, "done", progress_payload(run))
        except Exception as exc:
            logger.exception("Evaluation %s failed", run_id)
            await db.rollback()
            await update_run_status(db, run_id, RunStatus.FAILED, passed=False, error_message=str(exc))
            failed_run = await get_run_by_id(db, run_id)
            await notify(db, "error", progress_payload(failed_run))
            # Deterministic configuration/scoring errors must not be retried into
            # duplicate rows. A new request explicitly starts a new evaluation.
            raise
        finally:
            await redis.aclose()


def dispatch_eval_task(run_id, commit_sha, branch, question_set_path="tests/test_suite.json",
                       baseline_run_id=None, rag_endpoint=None, baseline_bootstrap=False):
    if not rag_endpoint:
        raise ValueError("rag_endpoint is required; provide the HTTP(S) RAG query URL")
    return run_evaluation_task.apply_async(kwargs=dict(run_id=run_id, commit_sha=commit_sha,
        branch=branch, question_set_path=question_set_path, baseline_run_id=baseline_run_id,
        rag_endpoint=rag_endpoint, baseline_bootstrap=baseline_bootstrap)).id
