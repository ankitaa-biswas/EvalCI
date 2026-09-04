"""
db/models.py
────────────
SQLAlchemy ORM models for EvalCI database.
"""

import enum
import os
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base

# The user requested to use declarative_base
Base = declarative_base()


# ---------------------------------------------------------------------------
# RunStatus — string enum used by crud.py to set / filter EvalRun.status
# ---------------------------------------------------------------------------


class RunStatus(str, enum.Enum):
    """Valid states for an EvalRun lifecycle."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# EvalRun — extended with all columns crud.py writes to
# ---------------------------------------------------------------------------


class EvalRun(Base):
    """One CI evaluation run, triggered per commit."""

    __tablename__ = "eval_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    commit_sha = Column(String, nullable=False)
    branch = Column(String, nullable=False)
    triggered_by = Column(String, nullable=True)          # e.g. "github-actions"
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    status = Column(String, nullable=False)               # see RunStatus enum values
    overall_severity = Column(Float, nullable=True)
    overall_score = Column(Float, nullable=True)
    passed = Column(Boolean, nullable=True)
    error_message = Column(Text, nullable=True)
    celery_task_id = Column(String, nullable=True)

    def __repr__(self):
        return f"<EvalRun(id={self.id}, commit_sha='{self.commit_sha}', status='{self.status}')>"


# ---------------------------------------------------------------------------
# QuestionResult — kept as-is (already existed)
# ---------------------------------------------------------------------------


class QuestionResult(Base):
    """Per-question Ragas + judge scores for one evaluation run."""

    __tablename__ = "question_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("eval_runs.id"), nullable=False)
    question_id = Column(String, nullable=False)
    category = Column(String, nullable=False)
    answer = Column(Text, nullable=False)
    context_recall = Column(Float, nullable=False)
    context_precision = Column(Float, nullable=False)
    groundedness = Column(Float, nullable=False)
    correctness = Column(Float, nullable=False)
    judge_mean_score = Column(Float, nullable=True)
    judge_agreement = Column(Float, nullable=True)

    def __repr__(self):
        return f"<QuestionResult(id={self.id}, question_id='{self.question_id}', run_id={self.run_id})>"


# ---------------------------------------------------------------------------
# QuestionScore — flat per-question record with all raw fields crud.py needs
# ---------------------------------------------------------------------------


class QuestionScore(Base):
    """Detailed per-question evaluation record including raw LLM inputs/outputs."""

    __tablename__ = "question_scores"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("eval_runs.id"), nullable=False)
    question_id = Column(String, nullable=False)
    category = Column(String, nullable=False)
    question_text = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    contexts = Column(JSON, nullable=True)           # list[str] retrieved chunks
    ground_truth = Column(Text, nullable=True)
    correctness = Column(Float, nullable=False)
    grounding = Column(Float, nullable=False)
    hallucination_risk = Column(Float, nullable=False)
    context_recall = Column(Float, nullable=False)
    context_precision = Column(Float, nullable=False)
    from_cache = Column(Boolean, nullable=False, default=False)

    def __repr__(self):
        return f"<QuestionScore(id={self.id}, question_id='{self.question_id}', run_id={self.run_id})>"


# ---------------------------------------------------------------------------
# CategoryScore — aggregated per-category metrics for one run
# ---------------------------------------------------------------------------


class CategoryScore(Base):
    """Aggregated metric averages grouped by question category for one run."""

    __tablename__ = "category_scores"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("eval_runs.id"), nullable=False)
    category = Column(String, nullable=False)
    question_count = Column(Integer, nullable=False)
    avg_correctness = Column(Float, nullable=False)
    avg_grounding = Column(Float, nullable=False)
    avg_hallucination_risk = Column(Float, nullable=False)
    avg_context_recall = Column(Float, nullable=False)
    avg_context_precision = Column(Float, nullable=False)
    passed = Column(Boolean, nullable=False)

    def __repr__(self):
        return f"<CategoryScore(id={self.id}, run_id={self.run_id}, category='{self.category}')>"


# ---------------------------------------------------------------------------
# Fingerprint — kept as-is (already existed)
# ---------------------------------------------------------------------------


class Fingerprint(Base):
    """Regression Fingerprint attribution result for one run."""

    __tablename__ = "fingerprints"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("eval_runs.id"), nullable=False)
    dominant_failure = Column(String, nullable=False)
    severity = Column(Float, nullable=False)
    confidence = Column(String, nullable=False)
    attribution_retriever = Column(Float, nullable=False)
    attribution_generator = Column(Float, nullable=False)
    attribution_prompt = Column(Float, nullable=False)
    attribution_kb = Column(Float, nullable=False)

    def __repr__(self):
        return f"<Fingerprint(id={self.id}, run_id={self.run_id}, dominant_failure='{self.dominant_failure}')>"


# ---------------------------------------------------------------------------
# FingerprintReport — full structured report persisted by crud.save_fingerprint
# ---------------------------------------------------------------------------


class FingerprintReport(Base):
    """Full regression fingerprint report comparing a run against a baseline."""

    __tablename__ = "fingerprint_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("eval_runs.id"), nullable=False)
    baseline_run_id = Column(UUID(as_uuid=True), ForeignKey("eval_runs.id"), nullable=False)
    overall_regressed = Column(Boolean, nullable=False)
    top_failing_component = Column(String, nullable=True)
    summary = Column(Text, nullable=True)
    regressions = Column(JSON, nullable=True)    # list[dict] of per-category regressions
    action_items = Column(JSON, nullable=True)   # list[str]

    def __repr__(self):
        return (
            f"<FingerprintReport(id={self.id}, run_id={self.run_id}, "
            f"overall_regressed={self.overall_regressed})>"
        )


# ---------------------------------------------------------------------------
# FailureCluster — kept as-is (already existed)
# ---------------------------------------------------------------------------


class FailureCluster(Base):
    """Semantic cluster of failed questions for one evaluation run."""

    __tablename__ = "failure_clusters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("eval_runs.id"), nullable=False)
    cluster_label = Column(String, nullable=False)
    question_ids = Column(JSON, nullable=False)  # list of strings
    dominant_failure = Column(String, nullable=False)
    size = Column(Integer, nullable=False)

    def __repr__(self):
        return f"<FailureCluster(id={self.id}, run_id={self.run_id}, cluster_label='{self.cluster_label}')>"


# ---------------------------------------------------------------------------
# Sync engine (used by Alembic migrations only)
# ---------------------------------------------------------------------------

# Read DB URL from env var DATABASE_URL
database_url = os.getenv("DATABASE_URL")
if database_url:
    engine = create_engine(database_url)

