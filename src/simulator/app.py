"""FastAPI application providing the autonomous Keitaro Admin API simulator."""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, status
from pydantic import BaseModel

from src.simulator.storage import simulator_storage

simulator_app = FastAPI(
    title="Keitaro Admin API Simulator",
    description="Autonomous standalone simulator for Keitaro Admin API (v1) with synthetic catalog and demo controls.",
    version="1.0.0",
)


def _check_api_key(api_key: str | None = Header(None, alias="Api-Key")) -> None:
    """Validate that requests to /admin_api/v1 provide an Api-Key header."""
    if not api_key or not api_key.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Api-Key header is required",
        )


admin_router = APIRouter(prefix="/admin_api/v1", dependencies=[Depends(_check_api_key)])


# --- Health & Diagnostic Endpoints ---


@simulator_app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "keitaro_simulator"}


# --- Admin API v1 Endpoints ---


@admin_router.get("/domains")
def get_domains() -> list[dict[str, Any]]:
    return simulator_storage.get_domains()


@admin_router.get("/groups")
def get_groups(type: str | None = Query(None)) -> list[dict[str, Any]]:
    return simulator_storage.get_groups(group_type=type)


@admin_router.get("/traffic_sources")
def get_traffic_sources() -> list[dict[str, Any]]:
    return simulator_storage.get_traffic_sources()


@admin_router.get("/offers")
def get_offers() -> list[dict[str, Any]]:
    return simulator_storage.get_offers()


@admin_router.get("/offers/{offer_id}")
def get_offer(offer_id: int) -> dict[str, Any]:
    offer = simulator_storage.get_offer(offer_id)
    if not offer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Offer #{offer_id} not found in Keitaro",
        )
    return offer


@admin_router.get("/campaigns")
def get_campaigns() -> list[dict[str, Any]]:
    return simulator_storage.get_campaigns()


@admin_router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: int) -> dict[str, Any]:
    camp = simulator_storage.get_campaign(campaign_id)
    if not camp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign #{campaign_id} not found",
        )
    return camp


@admin_router.post("/campaigns", status_code=status.HTTP_201_CREATED)
async def create_campaign(request: Request) -> dict[str, Any]:
    payload = await request.json()
    return simulator_storage.create_campaign(payload)


@admin_router.get("/campaigns/{campaign_id}/streams")
def get_campaign_streams(campaign_id: int) -> list[dict[str, Any]]:
    return simulator_storage.get_campaign_streams(campaign_id)


@admin_router.post("/streams", status_code=status.HTTP_201_CREATED)
async def create_stream(request: Request) -> dict[str, Any]:
    payload = await request.json()
    return simulator_storage.create_stream(payload)


@admin_router.get("/streams/{stream_id}")
def get_stream(stream_id: int) -> dict[str, Any]:
    # Fault injection: read-back error
    if (
        simulator_storage.active_fault
        and simulator_storage.active_fault["type"] == "read_back_error"
    ):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Simulated read-back verification failure",
        )

    stream = simulator_storage.get_stream(stream_id)
    if not stream:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stream #{stream_id} not found",
        )
    return stream


@admin_router.put("/streams/{stream_id}")
async def update_stream(stream_id: int, request: Request) -> dict[str, Any]:
    # Check fault injection
    fault = simulator_storage.active_fault
    if fault:
        f_type = fault["type"]
        params = fault.get("params", {})
        if f_type == "timeout_on_update":
            sleep_sec = params.get("sleep", 0)
            if sleep_sec > 0:
                await asyncio.sleep(sleep_sec)
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Simulated Keitaro update timeout (504)",
            )
        elif f_type == "reject_update":
            status_code = params.get("status_code", status.HTTP_422_UNPROCESSABLE_ENTITY)
            msg = params.get("message", "Simulated Keitaro rejection")
            raise HTTPException(status_code=status_code, detail=msg)
        elif f_type == "external_conflict":
            # Simulate another user changing stream before this update lands
            stream = simulator_storage.get_stream(stream_id)
            if stream and stream.get("offers"):
                stream["offers"][0]["share"] = (stream["offers"][0]["share"] + 1) % 100
                simulator_storage.streams[stream_id] = stream

    payload = await request.json()
    updated = simulator_storage.update_stream(stream_id, payload)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stream #{stream_id} not found",
        )
    return updated


simulator_app.include_router(admin_router)


# --- Demo Control & Simulation Endpoints ---


class DeleteStreamOfferRequest(BaseModel):
    stream_id: int
    offer_id: int


class DisableGlobalOfferRequest(BaseModel):
    offer_id: int
    available: bool = False


class SetFaultRequest(BaseModel):
    fault: str
    params: dict[str, Any] | None = None


@simulator_app.post("/demo/reset")
def demo_reset() -> dict[str, str]:
    simulator_storage.reset()
    return {"status": "ok", "message": "Simulator reset to seed"}


@simulator_app.post("/demo/simulate/delete_stream_offer")
def demo_simulate_delete_stream_offer(req: DeleteStreamOfferRequest) -> dict[str, Any]:
    updated_stream = simulator_storage.delete_stream_offer(req.stream_id, req.offer_id)
    if not updated_stream:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stream #{req.stream_id} not found in simulator",
        )
    return {
        "status": "ok",
        "message": f"Offer #{req.offer_id} removed from stream #{req.stream_id} and remaining rebalanced",
        "stream": updated_stream,
    }


@simulator_app.post("/demo/simulate/disable_global_offer")
def demo_simulate_disable_global_offer(req: DisableGlobalOfferRequest) -> dict[str, Any]:
    ok = simulator_storage.disable_global_offer(req.offer_id, req.available)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Offer #{req.offer_id} not found in simulator catalog",
        )
    return {
        "status": "ok",
        "offer_id": req.offer_id,
        "available": req.available,
        "message": f"Global offer #{req.offer_id} available={req.available}",
    }


@simulator_app.post("/demo/simulate/fault")
def demo_simulate_fault(req: SetFaultRequest) -> dict[str, Any]:
    if req.fault == "none":
        simulator_storage.clear_fault()
        return {"status": "ok", "message": "Fault cleared"}
    simulator_storage.set_fault(req.fault, req.params)
    return {"status": "ok", "fault": req.fault, "params": req.params or {}}


@simulator_app.get("/demo/state")
def demo_get_state() -> dict[str, Any]:
    return {
        "offers": simulator_storage.get_offers(),
        "domains": simulator_storage.get_domains(),
        "groups": simulator_storage.get_groups(),
        "traffic_sources": simulator_storage.get_traffic_sources(),
        "campaigns": simulator_storage.get_campaigns(),
        "streams": list(simulator_storage.streams.values()),
        "active_fault": simulator_storage.active_fault,
    }
