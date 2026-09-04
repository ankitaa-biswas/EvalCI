# evalci/api/models/score.py
# Purpose: Pydantic models for individual question scores, aggregate category
# scores, and the Regression Fingerprint Report returned by the API.

from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Component attribution enum
# ---------------------------------------------------------------------------


class FailingComponent(str, Enum):
    """
    Identifies which RAG sub-system is responsible for a detected regression.

    RETRIEVER: The document retrieval step is returning worse chunks —
               evidenced by low grounding scores even when the generator
               phrasing is coherent.
    GENERATOR: The LLM generation step is producing hallucinations or
               incorrect answers despite being given relevant context —
               evidenced by high grounding but low correctness.
    BOTH: Both components show degraded signals simultaneously.
    UNKNOWN: Insufficient signal to attribute the failure.
    """

    RETRIEVER = "RETRIEVER"
    GENERATOR = "GENERATOR"
    BOTH = "BOTH"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Per-question score
# ---------------------------------------------------------------------------


class QuestionScore(BaseModel):
    """
    Raw Ragas scores for a single question in the test suite.

    Attributes:
        question_id: Unique ID from ``test_suite.json``.
        category: Question category label from ``categories.json``.
        question: The question text.
        answer: The RAG system's answer to the question.
        contexts: Retrieved document chunks used by the generator.
        ground_truth: Expected correct answer from the test suite.
        correctness: Ragas ``answer_correctness`` metric [0, 1].
        grounding: Ragas ``faithfulness`` metric [0, 1].  Measures whether
                   the answer is grounded in the retrieved contexts.
        hallucination_risk: 1 - grounding; higher is worse.
        context_recall: Ragas ``context_recall`` metric [0, 1].
        context_precision: Ragas ``context_precision`` metric [0, 1].
    """

    question_id: str
    category: str
    question: str
    answer: str
    contexts: list[str]
    ground_truth: str
    correctness: float = Field(..., ge=0.0, le=1.0)
    grounding: float = Field(..., ge=0.0, le=1.0)
    hallucination_risk: float = Field(..., ge=0.0, le=1.0)
    context_recall: float = Field(..., ge=0.0, le=1.0)
    context_precision: float = Field(..., ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Category-level aggregate
# ---------------------------------------------------------------------------


class CategoryScore(BaseModel):
    """
    Aggregated (mean) Ragas scores across all questions in a single category.

    Attributes:
        category: Category label.
        question_count: Number of questions in this category.
        avg_correctness: Mean correctness score.
        avg_grounding: Mean grounding (faithfulness) score.
        avg_hallucination_risk: Mean hallucination risk.
        avg_context_recall: Mean context recall.
        avg_context_precision: Mean context precision.
        passed: True if all averages exceed configured thresholds.
    """

    category: str
    question_count: int
    avg_correctness: float = Field(..., ge=0.0, le=1.0)
    avg_grounding: float = Field(..., ge=0.0, le=1.0)
    avg_hallucination_risk: float = Field(..., ge=0.0, le=1.0)
    avg_context_recall: float = Field(..., ge=0.0, le=1.0)
    avg_context_precision: float = Field(..., ge=0.0, le=1.0)
    passed: bool = True


# ---------------------------------------------------------------------------
# Regression Fingerprint Report
# ---------------------------------------------------------------------------


class CategoryRegression(BaseModel):
    """
    Describes the regression observed in a single category between the
    current run and the baseline run.

    Attributes:
        category: Category that regressed.
        metric: Which Ragas metric drove the regression (e.g. ``grounding``).
        baseline_score: Score in the baseline run.
        current_score: Score in the current run.
        delta: current_score - baseline_score (negative means regression).
        severity: ``LOW`` | ``MEDIUM`` | ``HIGH`` based on the delta magnitude.
        attributed_to: Which component is responsible (``FailingComponent``).
        evidence: Human-readable explanation of the attribution logic.
        suggested_fix: Recommended remediation action.
    """

    category: str
    metric: str
    baseline_score: float
    current_score: float
    delta: float
    severity: str  # LOW | MEDIUM | HIGH
    attributed_to: FailingComponent
    evidence: str
    suggested_fix: str


class FingerprintReport(BaseModel):
    """
    The Regression Fingerprint Report — EvalCI's core novel output.

    This report is generated by ``core.fingerprinter`` whenever a completed
    run's scores fall below those of the baseline run.  It answers two
    questions that traditional CI eval tools cannot:

        1. *Which* categories of questions regressed?
        2. *Why* — was it the retriever or the generator that broke?

    The attribution logic (see ``core/fingerprinter.py``) works by examining
    the divergence pattern between the ``grounding`` (faithfulness) and
    ``correctness`` metrics:
        - High grounding + low correctness → GENERATOR failure.
        - Low grounding + low correctness  → RETRIEVER failure (bad context
          propagates to bad answers).
        - Low grounding + high correctness → BOTH (lucky correct answers
          despite bad retrieval — fragile, flag as warning).

    Attributes:
        run_id: The current evaluation run.
        baseline_run_id: The run used as the comparison baseline.
        overall_regressed: True if any metric fell below threshold.
        regressions: List of per-category regression objects.
        top_failing_component: The component attributed to the most severe
                               regressions across all categories.
        summary: Plain-English one-paragraph summary of the fingerprint.
        action_items: Prioritised list of recommended fixes.
    """

    run_id: UUID
    baseline_run_id: UUID
    overall_regressed: bool
    regressions: list[CategoryRegression]
    top_failing_component: FailingComponent
    summary: str
    action_items: list[str]
