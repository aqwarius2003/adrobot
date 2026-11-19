import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.logging import logger
from src.domain.models import Campaign, Offer, Operation, Stream, StreamOffer
from src.domain.schemas import CampaignCreateRequest, CampaignCreateResponse
from src.services.keitaro_client import KeitaroApiError, KeitaroGateway


class CampaignCreatorService:
    """Domain service responsible for Campaign creation with two routing flows."""

    def __init__(self, db: Session, kt_client: KeitaroGateway):
        self.db = db
        self.kt_client = kt_client

    def normalize_campaign_name(self, raw_name: str) -> str:
        surname = settings.CAMPAIGN_SURNAME.strip().lower()
        expected_prefix = f"test_{surname}_"
        clean = raw_name.strip()
        if not clean.lower().startswith(expected_prefix):
            return f"{expected_prefix}{clean}"
        return clean

    def normalize_geo(self, raw_geo: str) -> list[str]:
        if not raw_geo or not raw_geo.strip():
            raise ValueError("Country code(s) cannot be empty")

        codes = [c.strip().upper() for c in raw_geo.split(",") if c.strip()]
        if not codes:
            raise ValueError("At least one valid 2-letter country code is required")

        # Basic ISO alpha-2 regex
        for code in codes:
            if not re.match(r"^[A-Z]{2}$", code):
                raise ValueError(f"Invalid country code '{code}': must be a 2-letter ISO code")

        # Preserve order while eliminating duplicates
        seen = set()
        unique_codes = []
        for c in codes:
            if c not in seen:
                seen.add(c)
                unique_codes.append(c)
        return unique_codes

    def generate_alias(self, normalized_name: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized_name.lower()).strip("-")
        suffix = uuid.uuid4().hex[:6]
        return f"{slug}-{suffix}"

    async def create_campaign(self, request: CampaignCreateRequest) -> CampaignCreateResponse:
        idempotency_key = request.idempotency_key or str(uuid.uuid4())

        # Check existing operation
        op = self.db.query(Operation).filter_by(idempotency_key=idempotency_key).first()
        if op and op.status == "success" and op.remote_ids:
            # Return cached success
            remote_ids = op.remote_ids
            return CampaignCreateResponse(
                id=op.target_id or 0,
                remote_campaign_id=remote_ids.get("campaign_id", 0),
                name=request.name,
                alias=remote_ids.get("alias", ""),
                tracking_url=remote_ids.get("tracking_url"),
                flow1_stream_id=remote_ids.get("flow1_id"),
                flow2_stream_id=remote_ids.get("flow2_id"),
                routing_summary=f"Flow 1: {request.geo} -> Google; Flow 2: rest -> Offer {request.offer_id} (100%)",
                verification_status="verified",
            )

        if not op:
            op = Operation(
                idempotency_key=idempotency_key,
                operation_type="create_campaign",
                stage="validating",
                status="in_progress",
                candidate_payload=request.model_dump(),
            )
            self.db.add(op)
            self.db.commit()

        # Step 1: Validate inputs
        normalized_name = self.normalize_campaign_name(request.name)
        geo_codes = self.normalize_geo(request.geo)
        alias = self.generate_alias(normalized_name)

        # Step 2: Validate selected offer in Keitaro
        op.stage = "verifying_offer"
        self.db.commit()

        try:
            offer_data = await self.kt_client.get_offer(request.offer_id)
            offer_name = offer_data.get("name", f"Offer #{request.offer_id}")
        except Exception as exc:
            op.status = "failed"
            op.error_message = f"Offer {request.offer_id} validation failed: {exc}"
            self.db.commit()
            raise ValueError(
                f"Offer {request.offer_id} not found or unavailable in Keitaro: {exc}"
            ) from exc

        # Step 3: Determine domain, group, traffic_source
        domain_id = request.domain_id or settings.DEFAULT_DOMAIN_ID
        domain_url = None
        if not domain_id:
            domains = await self.kt_client.get_domains()
            if domains:
                domain_id = domains[0].get("id")
                domain_url = domains[0].get("name") or domains[0].get("domain")

        group_id = request.group_id or settings.DEFAULT_GROUP_ID
        if not group_id:
            groups = await self.kt_client.get_groups()
            if groups:
                group_id = str(groups[0].get("id"))
        else:
            group_id = str(group_id)

        traffic_source_id = request.traffic_source_id or settings.DEFAULT_TRAFFIC_SOURCE_ID
        if not traffic_source_id:
            sources = await self.kt_client.get_traffic_sources()
            if sources:
                traffic_source_id = sources[0].get("id")

        # Step 4: Create Campaign in Keitaro
        op.stage = "creating_campaign"
        self.db.commit()

        campaign_payload: dict[str, Any] = {
            "name": normalized_name,
            "alias": alias,
            "type": "position",
            "state": "active",
        }
        if domain_id:
            campaign_payload["domain_id"] = int(domain_id)
        if group_id:
            campaign_payload["group_id"] = str(group_id)
        if traffic_source_id:
            campaign_payload["traffic_source_id"] = int(traffic_source_id)

        try:
            remote_campaign = await self.kt_client.create_campaign(campaign_payload)
            remote_campaign_id = remote_campaign["id"]
        except Exception as exc:
            op.status = "failed"
            op.error_message = f"Failed to create campaign in Keitaro: {exc}"
            self.db.commit()
            raise KeitaroApiError(f"Failed to create campaign in Keitaro: {exc}") from exc

        # Step 5: Create Flow 1 (GEO -> Google redirect)
        op.stage = "creating_flow1"
        self.db.commit()

        flow1_payload = {
            "campaign_id": remote_campaign_id,
            "type": "regular",
            "name": "Flow 1",
            "position": 1,
            "state": "active",
            "schema": "redirect",
            "action_type": "http",
            "action_payload": "https://google.com/",
            "collect_clicks": True,
            "filters": [
                {
                    "name": "country",
                    "mode": "accept",
                    "payload": geo_codes,
                }
            ],
        }

        try:
            remote_flow1 = await self.kt_client.create_stream(flow1_payload)
            flow1_id = remote_flow1["id"]
        except Exception as exc:
            logger.error("Failed creating Flow 1: %s", exc)
            flow1_id = None

        # Step 6: Create Flow 2 (Default -> Offer 100%)
        op.stage = "creating_flow2"
        self.db.commit()

        flow2_payload = {
            "campaign_id": remote_campaign_id,
            "type": "default",
            "name": "Flow 2",
            "position": 2,
            "state": "active",
            "schema": "landings",
            "action_type": "http",
            "collect_clicks": True,
            "filters": [],
            "landings": [],
            "offers": [
                {
                    "offer_id": request.offer_id,
                    "share": 100,
                    "state": "active",
                }
            ],
        }

        try:
            remote_flow2 = await self.kt_client.create_stream(flow2_payload)
            flow2_id = remote_flow2["id"]
        except Exception as exc:
            logger.error("Failed creating Flow 2: %s", exc)
            flow2_id = None

        # Step 7: Read-back Verification
        op.stage = "verifying"
        self.db.commit()

        verification_status = "verified"
        try:
            read_back_streams = await self.kt_client.get_campaign_streams(remote_campaign_id)
            if len(read_back_streams) < 2:
                verification_status = "partial_streams_count"
        except Exception as exc:
            logger.warning("Read-back verification encountered error: %s", exc)
            verification_status = "read_back_failed"

        # Calculate tracking URL
        tracking_url = (
            f"{domain_url.rstrip('/')}/{alias}"
            if domain_url
            else f"{settings.normalized_base_url}/{alias}"
        )

        # Step 8: Persist in local database
        local_campaign = Campaign(
            remote_campaign_id=remote_campaign_id,
            name=normalized_name,
            alias=alias,
            domain_id=domain_id,
            domain_url=domain_url,
            group_id=str(group_id) if group_id else None,
            traffic_source_id=traffic_source_id,
        )
        self.db.add(local_campaign)
        self.db.flush()

        # Local cache for offer
        local_offer = self.db.query(Offer).filter_by(remote_offer_id=request.offer_id).first()
        if not local_offer:
            local_offer = Offer(
                remote_offer_id=request.offer_id,
                name=offer_name,
                is_available=True,
            )
            self.db.add(local_offer)
            self.db.flush()

        # Flow 1 (Opaque stream in Editor)
        if flow1_id:
            s1 = Stream(
                campaign_id=local_campaign.id,
                remote_stream_id=flow1_id,
                name="Flow 1",
                position=1,
                is_managed=False,  # Opaque stream
                stream_type="regular",
                stream_schema="redirect",
                action_type="http",
                action_payload="https://google.com/",
                sync_status="clean",
                raw_remote_config=flow1_payload,
            )
            self.db.add(s1)

        # Flow 2 (Managed offers stream in Editor)
        if flow2_id:
            s2 = Stream(
                campaign_id=local_campaign.id,
                remote_stream_id=flow2_id,
                name="Flow 2",
                position=2,
                is_managed=True,  # Managed offers stream
                stream_type="default",
                stream_schema="landings",
                action_type="http",
                sync_status="clean",
                raw_remote_config=flow2_payload,
            )
            self.db.add(s2)
            self.db.flush()

            so = StreamOffer(
                stream_id=s2.id,
                offer_id=local_offer.id,
                position=1,
                baseline_share=100,
                baseline_state="active",
                baseline_pinned=False,
                draft_share=100,
                draft_state="active",
                draft_pinned=False,
                has_been_published=True,
            )
            self.db.add(so)

        # Update Operation
        op.stage = "completed"
        op.status = "success"
        op.target_id = local_campaign.id
        op.remote_ids = {
            "campaign_id": remote_campaign_id,
            "alias": alias,
            "tracking_url": tracking_url,
            "flow1_id": flow1_id,
            "flow2_id": flow2_id,
        }
        self.db.commit()

        routing_summary = (
            f"Flow 1 ({flow1_id}): {', '.join(geo_codes)} -> https://google.com/; "
            f"Flow 2 ({flow2_id}): остальные -> {offer_name} [ID: {request.offer_id}] (100%)"
        )

        return CampaignCreateResponse(
            id=local_campaign.id,
            remote_campaign_id=remote_campaign_id,
            name=normalized_name,
            alias=alias,
            tracking_url=tracking_url,
            flow1_stream_id=flow1_id,
            flow2_stream_id=flow2_id,
            routing_summary=routing_summary,
            verification_status=verification_status,
        )
