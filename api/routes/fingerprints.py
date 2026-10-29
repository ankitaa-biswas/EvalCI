"""Optional /fingerprints router using the same canonical report as /results."""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from api.routes.results import get_fingerprint as get_report
from core.reports import FingerprintReport
from db.database import get_db

router = APIRouter()


@router.get("/{run_id}", response_model=FingerprintReport)
async def get_fingerprint(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return await get_report(str(run_id), db)


@router.get("/{run_id}/compare/{baseline_run_id}", response_model=FingerprintReport)
async def compare_fingerprints(run_id: uuid.UUID, baseline_run_id: uuid.UUID,
                               db: AsyncSession = Depends(get_db)):
    report = await get_report(str(run_id), db)
    if report.baseline_run_id != baseline_run_id:
        raise HTTPException(status_code=409, detail="This run used a different baseline. Submit a new evaluation with the requested explicit baseline_run_id.")
    return report
