from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse

from src.api.dependencies import get_creator_service, get_editor_service, get_keitaro_client
from src.core.config import settings
from src.core.connection_context import ConnectionContext, get_connection_context
from src.core.logging import logger
from src.domain.campaign_names import CampaignNameConflictError
from src.domain.iso_countries import COUNTRY_CATALOG
from src.domain.schemas import (
    CampaignCreateRequest,
    CampaignCreateResponse,
    CampaignDetailDTO,
    CampaignSummaryDTO,
    CountryCatalogItem,
    LookupItem,
    LookupOptionsResponse,
    OfferSearchItem,
)
from src.services.creator_service import CampaignCreatorService
from src.services.editor_service import (
    EditorConflictError,
    EditorValidationError,
    StreamEditorService,
)
from src.services.keitaro_client import KeitaroApiError, KeitaroGateway

router = APIRouter(tags=["Campaigns"])


@router.get("/geo/countries", response_model=list[CountryCatalogItem])
async def get_geo_countries(response: Response) -> list[dict]:
    """Return complete authoritative ISO 3166-1 alpha-2 country catalog."""
    response.headers["Cache-Control"] = "public, max-age=86400"
    return COUNTRY_CATALOG


@router.get("/options", response_model=LookupOptionsResponse)
async def get_options(
    kt_client: KeitaroGateway = Depends(get_keitaro_client),
) -> LookupOptionsResponse:
    try:
        raw_domains = await kt_client.get_domains()
        domains = [
            LookupItem(id=d["id"], name=d.get("name") or d.get("domain") or f"Domain #{d['id']}")
            for d in raw_domains
            if "id" in d
        ]
    except Exception:
        domains = []

    try:
        raw_groups = await kt_client.get_groups()
        groups = [
            LookupItem(id=int(g["id"]), name=g.get("name") or f"Group #{g['id']}")
            for g in raw_groups
            if "id" in g
        ]
    except Exception:
        groups = []

    try:
        raw_sources = await kt_client.get_traffic_sources()
        sources = [
            LookupItem(id=s["id"], name=s.get("name") or f"Source #{s['id']}")
            for s in raw_sources
            if "id" in s
        ]
    except Exception:
        sources = []

    return LookupOptionsResponse(
        domains=domains,
        groups=groups,
        traffic_sources=sources,
    )


@router.get("/offers", response_model=list[OfferSearchItem])
async def search_offers(
    search: str = Query("", description="Search term for offer name or numeric ID"),
    kt_client: KeitaroGateway = Depends(get_keitaro_client),
) -> list[OfferSearchItem]:
    try:
        raw_offers = await kt_client.get_offers()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch offers from Keitaro: {exc}",
        ) from exc

    results: list[OfferSearchItem] = []
    query = search.strip().lower()

    for off in raw_offers:
        off_id = off.get("id")
        name = off.get("name") or f"Offer #{off_id}"
        if not query:
            results.append(OfferSearchItem(id=off_id, name=name))
        else:
            if query in str(off_id) or query in name.lower():
                results.append(OfferSearchItem(id=off_id, name=name))

    return results[:50]


@router.get("/campaigns", response_model=list[CampaignSummaryDTO])
async def list_campaigns(
    kt_client: KeitaroGateway = Depends(get_keitaro_client),
) -> list[CampaignSummaryDTO]:
    try:
        raw_campaigns = await kt_client.get_campaigns()
        return [
            CampaignSummaryDTO(
                id=c["id"],
                remote_campaign_id=c["id"],
                name=c.get("name", f"Campaign #{c['id']}"),
                alias=c.get("alias", ""),
            )
            for c in raw_campaigns
            if "id" in c
        ]
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to retrieve campaigns from Keitaro: {exc}",
        ) from exc


@router.post(
    "/campaigns", response_model=CampaignCreateResponse, status_code=status.HTTP_201_CREATED
)
async def create_campaign(
    request: CampaignCreateRequest,
    _context: ConnectionContext = Depends(get_connection_context),
    creator_service: CampaignCreatorService = Depends(get_creator_service),
) -> CampaignCreateResponse | JSONResponse:
    try:
        return await creator_service.create_campaign(request)
    except CampaignNameConflictError as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc), "code": "campaign_name_exists", "field": "name"},
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except KeitaroApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/campaigns/{id}", response_model=CampaignDetailDTO)
def get_campaign_detail(
    id: int,
    editor_service: StreamEditorService = Depends(get_editor_service),
) -> CampaignDetailDTO:
    try:
        return editor_service.get_campaign_detail(id)
    except EditorValidationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/campaigns/{id}/fetch-streams", response_model=CampaignDetailDTO)
async def fetch_campaign_streams(
    id: int,
    editor_service: StreamEditorService = Depends(get_editor_service),
) -> CampaignDetailDTO:
    try:
        return await editor_service.fetch_campaign_streams(id)
    except EditorConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except KeitaroApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/campaigns/{id}/stream-stats")
async def get_campaign_stream_stats(
    id: int,
    kt_client: KeitaroGateway = Depends(get_keitaro_client),
) -> dict[str, Any]:
    """Retrieve today's clicks for streams in the specified campaign via Keitaro report API."""
    payload = {
        "range": {
            "interval": "today",
            "timezone": settings.REPORT_TIMEZONE,
        },
        "dimensions": ["stream_id"],
        "measures": ["clicks"],
        "filters": [
            {
                "name": "campaign_id",
                "operator": "EQUALS",
                "expression": id,
            }
        ],
    }
    try:
        report = await kt_client.build_report(payload)
        rows = report.get("rows") if isinstance(report, dict) else []
        clicks_by_stream = {
            int(r["stream_id"]): int(r.get("clicks", 0)) for r in (rows or []) if "stream_id" in r
        }
        return {
            "status": "ok",
            "campaign_id": id,
            "clicks_by_stream": clicks_by_stream,
            "timezone": settings.REPORT_TIMEZONE,
        }
    except Exception as exc:
        logger.warning("Failed to fetch stream clicks stats for campaign %s: %s", id, exc)
        return {
            "status": "unavailable",
            "campaign_id": id,
            "clicks_by_stream": {},
            "timezone": settings.REPORT_TIMEZONE,
            "error": str(exc),
        }
