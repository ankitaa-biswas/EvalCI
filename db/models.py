"""
db/models.py
────────────
SQLAlchemy ORM models for EvalCI database.
"""

import os
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
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


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    commit_sha = Column(String, nullable=False)
    branch = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, nullable=False)  # "pending", "running", "complete", "failed"
    overall_severity = Column(Float, nullable=True)

    def __repr__(self):
        return f"<EvalRun(id={self.id}, commit_sha='{self.commit_sha}', status='{self.status}')>"


class QuestionResult(Base):
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


class Fingerprint(Base):
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


class FailureCluster(Base):
    __tablename__ = "failure_clusters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("eval_runs.id"), nullable=False)
    cluster_label = Column(String, nullable=False)
    question_ids = Column(JSON, nullable=False)  # list of strings
    dominant_failure = Column(String, nullable=False)
    size = Column(Integer, nullable=False)

    def __repr__(self):
        return f"<FailureCluster(id={self.id}, run_id={self.run_id}, cluster_label='{self.cluster_label}')>"


# Read DB URL from env var DATABASE_URL
database_url = os.getenv("DATABASE_URL")
if database_url:
    engine = create_engine(database_url)
