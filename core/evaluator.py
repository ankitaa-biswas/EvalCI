"""
core/evaluator.py
─────────────────
Ragas scoring orchestration for EvalCI evaluation runs.

This module provides ``RAGEvaluator``, a synchronous evaluator that:

1. Calls a live RAG endpoint (via ``httpx``) for each test question.
2. Scores each (question, answer, retrieved_chunks, ground_truth) tuple with
   four Ragas metrics: context_recall, context_precision, faithfulness
   (groundedness), and answer_correctness.
3. Returns a structured list of per-question result dicts.

It also exposes two helpers used by the async pipeline (``score_to_dict`` and
``aggregate_by_category``) so they can be shared without circular imports.

Dependencies: ``httpx``, ``ragas>=0.1.9,<0.2.0``, ``datasets``.
"""

from __future__ import annotations

import logging
import statistics
from typing import Any

import httpx
from datasets import Dataset
from ragas import evaluate as ragas_evaluate
from ragas.metrics import (
    answer_correctness,
    context_precision,
    context_recall,
    faithfulness,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Ragas metric bundle
# ─────────────────────────────────────────────────────────────────────────────

#: The four Ragas metrics computed for every question. Order matters for
#: column alignment in the resulting DataFrame.
_RAGAS_METRICS = [
    answer_correctness,   # correctness: semantic match to ground_truth
    faithfulness,         # groundedness: answer grounded in retrieved chunks
    context_recall,       # was the relevant info present in retrieved chunks?
    context_precision,    # how many retrieved chunks were actually relevant?
]


# ─────────────────────────────────────────────────────────────────────────────
# Public class
# ─────────────────────────────────────────────────────────────────────────────


class RAGEvaluator:
    """Evaluate a RAG system against a curated test suite using Ragas metrics.

    The evaluator is intentionally **synchronous and stateless** so that it
    can be called from a Celery worker, a CLI script, or unit tests without
    requiring an async event loop.

    Typical usage::

        evaluator = RAGEvaluator(timeout=30.0)
        results = evaluator.evaluate(questions, rag_endpoint="http://localhost:8000/query")

    Each item in *results* is a dict with::

        {
            "question_id":      str,
            "category":         str,
            "answer":           str,
            "retrieved_chunks": list[str],
            "scores": {
                "context_recall":    float,  # 0.0 – 1.0
                "context_precision": float,  # 0.0 – 1.0
                "groundedness":      float,  # 0.0 – 1.0  (= ragas faithfulness)
                "correctness":       float,  # 0.0 – 1.0  (= ragas answer_correctness)
            },
        }
    """

    def __init__(self, timeout: float = 30.0) -> None:
        """Initialise the evaluator.

        Args:
            timeout: Seconds to wait for each RAG endpoint HTTP response before
                treating the question as failed and skipping it.
        """
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        questions: list[dict],
        rag_endpoint: str,
    ) -> list[dict]:
        """Run the full evaluate pipeline for a list of test questions.

        Steps
        ─────
        1. **RAG query** — POST ``{"query": question}`` to *rag_endpoint* and
           collect ``{"answer": str, "retrieved_chunks": list[str]}``.
           Failed requests are logged and the question is skipped gracefully.
        2. **Ragas dataset** — Assemble the surviving questions into a
           HuggingFace ``Dataset`` in the format Ragas expects.
        3. **Ragas scoring** — Run all four metrics in a single ``evaluate``
           call to minimise LLM API overhead.
        4. **Result packaging** — Merge question metadata, RAG output, and
           Ragas scores into a clean result dict per question.

        Args:
            questions: List of question dicts.  Each must contain:
                - ``id``           (str) — unique question identifier
                - ``question``     (str) — the question text
                - ``ground_truth`` (str) — the expected correct answer
                - ``category``     (str) — semantic category label
            rag_endpoint: Full URL of the RAG query endpoint, e.g.
                ``"http://localhost:8080/query"``.

        Returns:
            List of result dicts (one per successfully processed question).
            Questions that fail at the HTTP step are omitted from the output.
        """
        # ── Step 1: Call the RAG endpoint for each question ───────────────────
        successful_questions, rag_outputs = self._query_rag_bulk(
            questions, rag_endpoint
        )

        if not successful_questions:
            logger.warning(
                "evaluate: all %d questions failed RAG endpoint calls — "
                "returning empty results.",
                len(questions),
            )
            return []

        # ── Step 2: Build the Ragas-compatible HuggingFace Dataset ────────────
        dataset = self._build_ragas_dataset(successful_questions, rag_outputs)

        # ── Step 3: Score with Ragas ──────────────────────────────────────────
        ragas_df = self._run_ragas(dataset)

        # ── Step 4: Package results ───────────────────────────────────────────
        results: list[dict] = []
        for idx, (q, rag_out) in enumerate(zip(successful_questions, rag_outputs)):
            row = ragas_df.iloc[idx].to_dict()
            results.append(self._package_result(q, rag_out, row))

        logger.info(
            "evaluate: scored %d/%d questions successfully.",
            len(results),
            len(questions),
        )
        return results

    # ------------------------------------------------------------------
    # Step 1 — RAG endpoint calls
    # ------------------------------------------------------------------

    def _query_rag_bulk(
        self,
        questions: list[dict],
        rag_endpoint: str,
    ) -> tuple[list[dict], list[dict]]:
        """POST each question to the RAG endpoint; skip failures gracefully.

        Uses a single ``httpx.Client`` for connection-pool efficiency across
        all questions in the batch.

        Args:
            questions:    Full list of question dicts from the caller.
            rag_endpoint: URL to POST ``{"query": ...}`` to.

        Returns:
            A 2-tuple of parallel lists:
            - ``successful_questions``: subset of *questions* that got a response.
            - ``rag_outputs``:          corresponding RAG response dicts with keys
              ``answer`` (str) and ``retrieved_chunks`` (list[str]).
        """
        successful_questions: list[dict] = []
        rag_outputs: list[dict] = []

        with httpx.Client(timeout=self._timeout) as client:
            for q in questions:
                try:
                    rag_out = self._call_rag_endpoint(client, rag_endpoint, q)
                    successful_questions.append(q)
                    rag_outputs.append(rag_out)
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "RAG endpoint call failed for question '%s' (id=%s): %s — skipping.",
                        q.get("question", "")[:80],
                        q.get("id", "unknown"),
                        exc,
                    )

        return successful_questions, rag_outputs

    @staticmethod
    def _call_rag_endpoint(
        client: httpx.Client,
        rag_endpoint: str,
        question: dict,
    ) -> dict:
        """Send a single POST request and parse the RAG response.

        Args:
            client:       Shared ``httpx.Client`` instance (connection pool).
            rag_endpoint: Full URL of the RAG query endpoint.
            question:     Question dict containing at least the ``"question"`` key.

        Returns:
            Dict with:
            - ``answer``           (str)       — the generated answer text.
            - ``retrieved_chunks`` (list[str]) — text of the retrieved passages.

        Raises:
            httpx.HTTPStatusError: If the endpoint returns a 4xx / 5xx status.
            httpx.RequestError:    On network-level failures (timeout, DNS, etc.).
            KeyError:              If the response JSON is missing required fields.
        """
        response = client.post(
            rag_endpoint,
            json={"query": question["question"]},
        )
        response.raise_for_status()
        payload = response.json()

        answer: str = payload["answer"]
        # Accept both "retrieved_chunks" and the legacy "contexts" key so the
        # evaluator works with both the built-in rag_target and external services.
        retrieved_chunks: list[str] = payload.get(
            "retrieved_chunks", payload.get("contexts", [])
        )
        return {"answer": answer, "retrieved_chunks": retrieved_chunks}

    # ------------------------------------------------------------------
    # Step 2 — Dataset construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_ragas_dataset(
        questions: list[dict],
        rag_outputs: list[dict],
    ) -> Dataset:
        """Assemble a HuggingFace ``Dataset`` in the schema Ragas expects.

        Ragas v0.1.x requires exactly these column names:
        - ``question``    — the query string
        - ``answer``      — the generated answer
        - ``contexts``    — list of retrieved passage strings (list[str])
        - ``ground_truth``— the reference correct answer

        Args:
            questions:   Subset of question dicts that passed the RAG call step.
            rag_outputs: Parallel list of RAG response dicts.

        Returns:
            A ``datasets.Dataset`` ready to be passed to ``ragas.evaluate``.
        """
        return Dataset.from_dict(
            {
                "question":    [q["question"]    for q in questions],
                "answer":      [r["answer"]       for r in rag_outputs],
                "contexts":    [r["retrieved_chunks"] for r in rag_outputs],
                "ground_truth":[q["ground_truth"] for q in questions],
            }
        )

    # ------------------------------------------------------------------
    # Step 3 — Ragas scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _run_ragas(dataset: Dataset):  # -> pd.DataFrame
        """Run Ragas evaluation and return results as a pandas DataFrame.

        Args:
            dataset: HuggingFace Dataset built by ``_build_ragas_dataset``.

        Returns:
            ``pandas.DataFrame`` with one row per question and columns for each
            metric: ``answer_correctness``, ``faithfulness``,
            ``context_recall``, ``context_precision``.

        Raises:
            Exception: Propagates any Ragas / OpenAI API errors to the caller.
        """
        result = ragas_evaluate(dataset, metrics=_RAGAS_METRICS)
        return result.to_pandas()

    # ------------------------------------------------------------------
    # Step 4 — Result packaging
    # ------------------------------------------------------------------

    @staticmethod
    def _package_result(
        question: dict,
        rag_output: dict,
        ragas_row: dict,
    ) -> dict:
        """Merge one question's metadata, RAG output, and Ragas row into a result dict.

        Ragas metric names are translated to EvalCI's canonical names:
        - ``faithfulness``       → ``groundedness``
        - ``answer_correctness`` → ``correctness``

        Missing or ``None`` values default to ``0.0`` to keep downstream
        arithmetic safe.

        Args:
            question:   The original question dict (``id``, ``category``, etc.).
            rag_output: The parsed RAG response dict.
            ragas_row:  One row from the Ragas results DataFrame as a plain dict.

        Returns:
            A result dict matching the documented output schema of ``evaluate``.
        """
        def _safe_float(key: str) -> float:
            """Extract a float from *ragas_row*, defaulting to 0.0 on None."""
            return float(ragas_row.get(key) or 0.0)

        return {
            "question_id":      question["id"],
            "category":         question["category"],
            "answer":           rag_output["answer"],
            "retrieved_chunks": rag_output["retrieved_chunks"],
            "scores": {
                "context_recall":    round(_safe_float("context_recall"),    4),
                "context_precision": round(_safe_float("context_precision"), 4),
                "groundedness":      round(_safe_float("faithfulness"),      4),
                "correctness":       round(_safe_float("answer_correctness"),4),
            },
        }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — used by the async pipeline in core/queue.py
# ─────────────────────────────────────────────────────────────────────────────


def score_to_dict(q: dict, rag_r: dict, ragas_row: dict) -> dict:
    """Merge question metadata + RAG output + Ragas metrics into a unified dict.

    This is the flat (non-nested) variant used by the Celery worker pipeline
    where individual fields are stored as DB columns.  The ``RAGEvaluator``
    uses the nested ``scores`` variant instead.

    Normalises metric names and computes ``hallucination_risk = 1 - faithfulness``.
    """
    grounding = float(ragas_row.get("faithfulness", 0.0) or 0.0)
    return {
        "question_id":       q["id"],
        "category":          q["category"],
        "question":          q["question"],
        "answer":            rag_r["answer"],
        "contexts":          rag_r.get("retrieved_chunks", rag_r.get("contexts", [])),
        "ground_truth":      q["ground_truth"],
        "correctness":       float(ragas_row.get("answer_correctness", 0.0) or 0.0),
        "grounding":         grounding,
        "hallucination_risk": round(max(0.0, 1.0 - grounding), 4),
        "context_recall":    float(ragas_row.get("context_recall", 0.0) or 0.0),
        "context_precision": float(ragas_row.get("context_precision", 0.0) or 0.0),
    }


def aggregate_by_category(
    scores: list[dict],
    category_thresholds: dict | None = None,
) -> dict[str, dict]:
    """Group per-question scores by category and compute per-category means.

    Args:
        scores:               List of score dicts from ``score_to_dict``.
        category_thresholds:  Optional mapping of ``{category: {metric: threshold}}``.
            Defaults to correctness ≥ 0.70 and grounding ≥ 0.75.

    Returns:
        ``{category: {avg_correctness, avg_grounding, avg_hallucination_risk,
        avg_context_recall, avg_context_precision, question_count, passed}}``.
    """
    groups: dict[str, list[dict]] = {}
    for s in scores:
        groups.setdefault(s["category"], []).append(s)

    thresholds = category_thresholds or {}
    aggregated: dict[str, dict] = {}

    for cat, cat_scores in groups.items():
        avg_correctness  = statistics.mean(s["correctness"]       for s in cat_scores)
        avg_grounding    = statistics.mean(s["grounding"]         for s in cat_scores)
        avg_hallucination= statistics.mean(s["hallucination_risk"] for s in cat_scores)
        avg_recall       = statistics.mean(s["context_recall"]    for s in cat_scores)
        avg_precision    = statistics.mean(s["context_precision"]  for s in cat_scores)

        cat_thresh = thresholds.get(cat, {})
        passed = (
            avg_correctness >= cat_thresh.get("correctness", 0.70)
            and avg_grounding >= cat_thresh.get("grounding", 0.75)
        )

        aggregated[cat] = {
            "question_count":        len(cat_scores),
            "avg_correctness":       round(avg_correctness,   4),
            "avg_grounding":         round(avg_grounding,     4),
            "avg_hallucination_risk":round(avg_hallucination, 4),
            "avg_context_recall":    round(avg_recall,        4),
            "avg_context_precision": round(avg_precision,     4),
            "passed":                passed,
        }

    return aggregated
