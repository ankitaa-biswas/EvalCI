"""Progress contract and durable PostgreSQL event log for SSE replay."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy import select

from core.reports import Contract
from db.models import RunEvent


class ProgressEvent(Contract):
    run_id: UUID
    stage: Literal["queued", "starting", "rag_requests", "scoring", "persisting", "complete", "failed"]
    completed: int = Field(ge=0, strict=True)
    total: int = Field(ge=1, strict=True)
    status: Literal["pending", "running", "complete", "failed"]
    attempted: int = Field(ge=0, strict=True)
    successfully_scored: int = Field(ge=0, strict=True)
    failed: int = Field(ge=0, strict=True)
    evaluation_mode: Literal["ragas", "fixture"] | None = None
    question_id: str | None = None
    error: str | None = None
    overall_score: float | None = None
    passed: bool | None = None
    fingerprint_available: bool = False
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def counters(self):
        if not self.completed <= self.total or not self.successfully_scored + self.failed <= self.attempted <= self.total:
            raise ValueError("Progress counters are inconsistent")
        return self


def progress_payload(run, stage=None):
    """Recover terminal state even if the worker died before logging its event."""
    counts = run.completion or {"expected": 1, "attempted": 0, "successfully_scored": 0, "failed": 0}
    status = str(getattr(run.status, "value", run.status))
    return ProgressEvent(run_id=run.id, stage=stage or ("complete" if status == "complete" else "failed" if status == "failed" else "starting"),
        completed=counts["successfully_scored"] + counts["failed"], total=counts["expected"], status=status,
        attempted=counts["attempted"], successfully_scored=counts["successfully_scored"], failed=counts["failed"],
        evaluation_mode=(run.provenance or {}).get("configuration", {}).get("scoring_mode"),
        error=run.error_message, overall_score=run.overall_score, passed=run.passed,
        fingerprint_available=status == "complete")


async def append_event(db, kind, event):
    event = ProgressEvent.model_validate(event)
    row = RunEvent(run_id=event.run_id, kind=kind, data=event.model_dump(mode="json"))
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def events_after(db, run_id, after=0):
    result = await db.execute(select(RunEvent).where(RunEvent.run_id == UUID(str(run_id)), RunEvent.id > after).order_by(RunEvent.id).limit(100))
    return list(result.scalars())
