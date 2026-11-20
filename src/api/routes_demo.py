"""Demo control routes for managing simulator state and reproducing demo scenarios."""

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.api.dependencies import get_db
from src.core.config import settings
from src.domain.models import Campaign, Operation, Stream, StreamOffer

router = APIRouter(prefix="/api/demo", tags=["Demo Controls"])


class DemoDeleteStreamOfferRequest(BaseModel):
    stream_id: int
    offer_id: int


class DemoDisableGlobalOfferRequest(BaseModel):
    offer_id: int
    available: bool = False


@router.post("/reset")
async def demo_reset(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Reset both the simulator state and the local application demo database."""
    if not settings.is_demo_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo reset is only available in demo mode (KT_MODE=demo)",
        )

    # 1. Reset simulator state via HTTP call
    sim_url = settings.DEMO_SIMULATOR_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.post(f"{sim_url}/demo/reset")
            if res.status_code != 200:
                raise HTTPException(
                    status_code=res.status_code,
                    detail=f"Simulator reset failed: {res.text}",
                )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not connect to simulator at {sim_url}: {exc}",
        ) from exc

    # 2. Reset local application tables in demo database
    from src.domain.models import Base

    Base.metadata.create_all(bind=db.get_bind())
    db.query(StreamOffer).delete()
    db.query(Stream).delete()
    db.query(Campaign).delete()
    db.query(Operation).delete()
    db.commit()

    return {"status": "ok", "message": "Demo state and local database reset successfully"}


@router.post("/simulate-delete-stream-offer")
async def demo_simulate_delete_stream_offer(
    req: DemoDeleteStreamOfferRequest,
) -> dict[str, Any]:
    """Simulate external deletion of an offer from a stream in the tracker."""
    if not settings.is_demo_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Simulation controls are only available in demo mode",
        )

    sim_url = settings.DEMO_SIMULATOR_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.post(
                f"{sim_url}/demo/simulate/delete_stream_offer",
                json=req.model_dump(),
            )
            if res.status_code != 200:
                raise HTTPException(status_code=res.status_code, detail=res.text)
            return res.json()
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not connect to simulator at {sim_url}: {exc}",
        ) from exc


@router.post("/simulate-disable-global-offer")
async def demo_simulate_disable_global_offer(
    req: DemoDisableGlobalOfferRequest,
) -> dict[str, Any]:
    """Simulate global offer becoming unavailable in tracker catalog."""
    if not settings.is_demo_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Simulation controls are only available in demo mode",
        )

    sim_url = settings.DEMO_SIMULATOR_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.post(
                f"{sim_url}/demo/simulate/disable_global_offer",
                json=req.model_dump(),
            )
            if res.status_code != 200:
                raise HTTPException(status_code=res.status_code, detail=res.text)
            return res.json()
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not connect to simulator at {sim_url}: {exc}",
        ) from exc


# ==============================================================================
# Simulator Tab Same-Origin API (DEMO_MODE_TASK §5)
# ==============================================================================


class UpdateSimulatorStreamOffersPayload(BaseModel):
    offers: list[dict[str, Any]]
    expected_fingerprint: str | None = None


@router.get("/simulator/campaigns")
async def get_simulator_campaigns():
    from src.services.demo_admin_service import DemoAdminService

    service = DemoAdminService()
    try:
        return await service.get_campaigns()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/simulator/campaigns/{campaign_id}/streams")
async def get_simulator_campaign_streams(campaign_id: int):
    from src.services.demo_admin_service import DemoAdminService

    service = DemoAdminService()
    try:
        return await service.get_campaign_streams(campaign_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/simulator/offers")
async def get_simulator_offers(search: str = ""):
    from src.services.demo_admin_service import DemoAdminService

    service = DemoAdminService()
    try:
        return await service.get_offers(search=search)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.put("/simulator/campaigns/{campaign_id}/streams/{stream_id}/offers")
async def save_simulator_stream_offers(
    campaign_id: int, stream_id: int, payload: UpdateSimulatorStreamOffersPayload
):
    from src.services.demo_admin_service import DemoAdminConflictError, DemoAdminService

    service = DemoAdminService()
    try:
        return await service.save_stream_offers(
            campaign_id=campaign_id,
            stream_id=stream_id,
            offers=payload.offers,
            expected_fingerprint=payload.expected_fingerprint,
        )
    except DemoAdminConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        try:
            err_data = exc.response.json()
            err_detail = err_data.get("detail", exc.response.text)
        except Exception:
            err_detail = exc.response.text
        raise HTTPException(status_code=exc.response.status_code, detail=err_detail) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
