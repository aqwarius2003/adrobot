from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.api.dependencies import get_db
from src.core.config import settings

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    status: str
    database: str
    version: str
    commit_sha: str
    keitaro_configured: bool
    kt_mode: str
    environment: str


@router.get("/api/health", response_model=HealthResponse, status_code=status.HTTP_200_OK)
def get_health(db: Session = Depends(get_db)) -> HealthResponse:
    db_status = "connected"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    return HealthResponse(
        status="ok" if db_status == "connected" else "degraded",
        database=db_status,
        version=settings.APP_VERSION,
        commit_sha=settings.COMMIT_SHA,
        keitaro_configured=settings.is_keitaro_configured,
        kt_mode=settings.default_mode,
        environment=settings.APP_ENV,
    )
