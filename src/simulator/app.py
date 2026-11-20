"""FastAPI application providing the autonomous Keitaro Admin API simulator."""

import asyncio
import hmac
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StrictInt, StrictStr, field_validator

from src.core.config import settings
from src.domain.campaign_names import CampaignNameConflictError
from src.simulator.storage import simulator_storage

simulator_app = FastAPI(
    title="Keitaro Admin API Simulator",
    description="Autonomous standalone simulator for Keitaro Admin API (v1) with synthetic catalog and demo controls.",
    version="1.0.0",
)


def _check_api_key(api_key: str | None = Header(None, alias="Api-Key")) -> None:
    """Validate that requests to /admin_api/v1 provide the configured synthetic Api-Key."""
    expected = settings.DEMO_KEITARO_API_KEY
    if not api_key or not hmac.compare_digest(api_key.strip(), expected.strip()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Api-Key header",
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
async def create_campaign(request: Request) -> Any:
    payload = await request.json()
    try:
        return simulator_storage.create_campaign(payload)
    except CampaignNameConflictError as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc), "code": "campaign_name_exists"},
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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


class StreamOfferPayload(BaseModel):
    offer_id: StrictInt = Field(gt=0, description="Positive integer offer ID")
    share: StrictInt = Field(ge=0, le=100, description="Integer percentage share between 0 and 100")
    state: Literal["active", "removed"] | None = "active"


class StreamUpdatePayload(BaseModel):
    name: str | None = None
    offers: list[StreamOfferPayload] | None = None

    @field_validator("offers")
    @classmethod
    def validate_offers(
        cls, offers: list[StreamOfferPayload] | None
    ) -> list[StreamOfferPayload] | None:
        if offers is None:
            return offers
        # Check for duplicate offer_id
        ids = [o.offer_id for o in offers]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate offer_ids in offers payload")
        # Validate sum of active shares
        active_shares = [o.share for o in offers if (o.state or "active") == "active"]
        if active_shares:
            if sum(active_shares) != 100:
                raise ValueError(
                    f"Sum of active shares must be strictly 100%, got {sum(active_shares)}%"
                )
        return offers


@admin_router.put("/streams/{stream_id}")
async def update_stream(stream_id: int, payload: StreamUpdatePayload) -> dict[str, Any]:
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

    data = payload.model_dump(exclude_unset=True)
    updated = simulator_storage.update_stream(stream_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stream #{stream_id} not found",
        )
    return updated


@admin_router.post("/report/build")
def build_report(req: dict[str, Any]) -> dict[str, Any]:
    """Simulate Keitaro report building endpoint (POST /admin_api/v1/report/build)."""
    if simulator_storage.active_fault:
        fault_type = simulator_storage.active_fault.get("type")
        if fault_type == "network_error":
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail="Simulated network error"
            )
        if fault_type == "report_forbidden":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Simulated 403 Forbidden on reports"
            )

    filters = req.get("filters") or []
    camp_id = None
    for f in filters:
        if f.get("name") == "campaign_id":
            try:
                camp_id = int(f.get("expression"))
            except (TypeError, ValueError):
                pass

    streams = list(simulator_storage.streams.values())
    if camp_id is not None:
        streams = [s for s in streams if s.get("campaign_id") == camp_id]

    rows = []
    for s in streams:
        rows.append(
            {
                "stream_id": s["id"],
                "clicks": s.get("clicks_today", 0),
            }
        )

    return {
        "rows": rows,
        "total": sum(r["clicks"] for r in rows),
    }


simulator_app.include_router(admin_router)

# --- Demo Control & Simulation Endpoints ---

demo_control_router = APIRouter(dependencies=[Depends(_check_api_key)])


class DeleteStreamOfferRequest(BaseModel):
    stream_id: StrictInt
    offer_id: StrictInt


class DisableGlobalOfferRequest(BaseModel):
    offer_id: StrictInt
    available: bool = False


class SetFaultRequest(BaseModel):
    fault: StrictStr
    params: dict[str, Any] | None = None


class UpdateSimulatorStreamOffersRequest(BaseModel):
    offers: list[StreamOfferPayload] = Field(..., max_length=100)
    expected_fingerprint: StrictStr | None = None


@demo_control_router.put("/demo/admin/campaigns/{campaign_id}/streams/{stream_id}/offers")
def update_simulator_stream_offers(
    campaign_id: int, stream_id: int, req: UpdateSimulatorStreamOffersRequest
) -> dict[str, Any]:
    try:
        raw_offers = [o.model_dump() for o in req.offers]
        return simulator_storage.update_stream_offers(
            campaign_id=campaign_id,
            stream_id=stream_id,
            offers=raw_offers,
            expected_fingerprint=req.expected_fingerprint,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        msg = str(exc)
        if "Conflict" in msg:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=msg) from exc
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=msg) from exc


@demo_control_router.post("/demo/reset")
def demo_reset() -> dict[str, str]:
    if settings.APP_ENV == "production":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Simulator reset is disabled in production",
        )
    simulator_storage.reset()
    return {"status": "ok", "message": "Simulator reset to seed"}


@demo_control_router.post("/demo/simulate/delete_stream_offer")
def demo_simulate_delete_stream_offer(req: DeleteStreamOfferRequest) -> dict[str, Any]:
    if settings.APP_ENV == "production":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Simulator fault injection is disabled in production",
        )
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


@demo_control_router.post("/demo/simulate/disable_global_offer")
def demo_simulate_disable_global_offer(req: DisableGlobalOfferRequest) -> dict[str, Any]:
    if settings.APP_ENV == "production":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Simulator fault injection is disabled in production",
        )
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


@demo_control_router.post("/demo/simulate/fault")
def demo_simulate_fault(req: SetFaultRequest) -> dict[str, Any]:
    if settings.APP_ENV == "production":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Simulator fault injection is disabled in production",
        )
    if req.fault == "none":
        simulator_storage.clear_fault()
        return {"status": "ok", "message": "Fault cleared"}
    simulator_storage.set_fault(req.fault, req.params)
    return {"status": "ok", "fault": req.fault, "params": req.params or {}}


@demo_control_router.get("/demo/state")
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


simulator_app.include_router(demo_control_router)
