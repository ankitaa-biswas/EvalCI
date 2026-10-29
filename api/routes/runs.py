"""Optional /runs router, sharing the /evaluate request and persisted report."""
import uuid
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from api.models.run import EvalRunRequest, EvalRunResponse
from api.routes.evaluate import trigger_evaluation
from api.routes.results import get_run as get_results, list_runs as list_results
from db.database import get_db

router = APIRouter()


@router.post("", response_model=EvalRunResponse, status_code=202)
async def create_run(body: EvalRunRequest, db: AsyncSession = Depends(get_db)):
    return await trigger_evaluation(body, db)


@router.get("/{run_id}")
async def get_run(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return await get_results(str(run_id), db)


@router.get("")
async def list_runs(branch: str | None = None, db: AsyncSession = Depends(get_db)):
    return await list_results(limit=50, offset=0, branch=branch, db=db)
