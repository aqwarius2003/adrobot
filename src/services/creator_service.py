import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.logging import logger
from src.domain.iso_countries import is_valid_iso_country
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

        for code in codes:
            if not is_valid_iso_country(code):
                raise ValueError(
                    f"Invalid country code '{code}': not a valid ISO 3166-1 alpha-2 country code"
                )

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

    def build_tracking_url(
        self,
        domain_url: str | None,
        alias: str,
        source_params: str | None = None,
    ) -> str:
        if domain_url:
            clean_domain = domain_url.strip().rstrip("/")
            if not clean_domain.startswith("http://") and not clean_domain.startswith("https://"):
                clean_domain = f"https://{clean_domain}"
            base = f"{clean_domain}/{alias}"
        else:
            base = f"{settings.normalized_base_url}/{alias}"

        if source_params:
            clean_params = source_params.strip().lstrip("?").strip()
            if clean_params:
                base = f"{base}?{clean_params}"
        return base

    async def create_campaign(self, request: CampaignCreateRequest) -> CampaignCreateResponse:
        idempotency_key = request.idempotency_key or str(uuid.uuid4())

        # Check existing operation
        op = self.db.query(Operation).filter_by(idempotency_key=idempotency_key).first()
        if op:
            # Check payload-key mismatch (F7)
            if op.candidate_payload:
                req_dict = request.model_dump()
                for key in ("name", "geo", "offer_id"):
                    if op.candidate_payload.get(key) != req_dict.get(key):
                        raise ValueError(
                            f"Idempotency key '{idempotency_key}' reused with different payload"
                        )

            if op.status == "success" and op.remote_ids:
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

        normalized_name = self.normalize_campaign_name(request.name)
        geo_codes = self.normalize_geo(request.geo)

        if not op:
            alias = self.generate_alias(normalized_name)
            candidate_payload = request.model_dump()
            candidate_payload["alias"] = alias
            candidate_payload["normalized_name"] = normalized_name

            op = Operation(
                idempotency_key=idempotency_key,
                operation_type="create_campaign",
                stage="validating",
                status="in_progress",
                candidate_payload=candidate_payload,
                remote_ids={},
            )
            self.db.add(op)
            self.db.commit()
        else:
            alias = op.candidate_payload.get("alias") or self.generate_alias(normalized_name)

        remote_ids: dict[str, Any] = dict(op.remote_ids or {})

        # Step 1: Validate selected offer in Keitaro
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

        # Step 2: Determine domain, group, traffic_source & parameters (F8)
        domain_id = request.domain_id or settings.DEFAULT_DOMAIN_ID
        domain_url = None
        domains = await self.kt_client.get_domains()
        if domain_id:
            for d in domains:
                if d.get("id") == int(domain_id):
                    domain_url = d.get("name") or d.get("domain")
                    break
        elif domains:
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
        source_params = None
        sources = await self.kt_client.get_traffic_sources()
        if traffic_source_id:
            for s in sources:
                if s.get("id") == int(traffic_source_id):
                    source_params = s.get("parameters") or s.get("template")
                    break
        elif sources:
            traffic_source_id = sources[0].get("id")
            source_params = sources[0].get("parameters") or sources[0].get("template")

        # Step 3: Create Campaign in Keitaro (with F7 duplicate search)
        remote_campaign_id = remote_ids.get("campaign_id")
        if not remote_campaign_id:
            op.stage = "creating_campaign"
            self.db.commit()

            # F7: check if campaign with this name/alias already exists (e.g. timeout on prior attempt)
            try:
                existing_campaigns = await self.kt_client.get_campaigns()
                for c in existing_campaigns:
                    if c.get("name") == normalized_name or c.get("alias") == alias:
                        remote_campaign_id = c["id"]
                        logger.info(
                            "Found existing campaign %s for %s after retry",
                            remote_campaign_id,
                            normalized_name,
                        )
                        break
            except Exception as exc:
                logger.warning("Could not check existing campaigns: %s", exc)

            if not remote_campaign_id:
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

            # Save confirmed campaign_id immediately (F6)
            remote_ids["campaign_id"] = remote_campaign_id
            remote_ids["alias"] = alias
            op.remote_ids = remote_ids
            self.db.commit()

        # Step 4: Create Flow 1 (GEO -> Google redirect)
        flow1_id = remote_ids.get("flow1_id")
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

        if not flow1_id:
            op.stage = "creating_flow1"
            self.db.commit()
            try:
                remote_flow1 = await self.kt_client.create_stream(flow1_payload)
                flow1_id = remote_flow1["id"]
                remote_ids["flow1_id"] = flow1_id
                op.remote_ids = remote_ids
                self.db.commit()
            except Exception as exc:
                op.status = "failed"
                op.error_message = f"Failed to create Flow 1 in Keitaro: {exc}"
                self.db.commit()
                raise KeitaroApiError(f"Failed to create Flow 1: {exc}") from exc

        # Step 5: Create Flow 2 (Default -> Offer 100%)
        flow2_id = remote_ids.get("flow2_id")
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

        if not flow2_id:
            op.stage = "creating_flow2"
            self.db.commit()
            try:
                remote_flow2 = await self.kt_client.create_stream(flow2_payload)
                flow2_id = remote_flow2["id"]
                remote_ids["flow2_id"] = flow2_id
                op.remote_ids = remote_ids
                self.db.commit()
            except Exception as exc:
                op.status = "failed"
                op.error_message = f"Failed to create Flow 2 in Keitaro: {exc}"
                self.db.commit()
                raise KeitaroApiError(f"Failed to create Flow 2: {exc}") from exc

        # Step 6: Deep Read-Back Verification (F6)
        op.stage = "verifying"
        self.db.commit()

        try:
            read_back_streams = await self.kt_client.get_campaign_streams(remote_campaign_id)
        except Exception as exc:
            op.status = "failed"
            op.error_message = f"Read-back fetch failed: {exc}"
            self.db.commit()
            raise KeitaroApiError(f"Read-back fetch failed: {exc}") from exc

        rb_flow1 = next((s for s in read_back_streams if s.get("id") == flow1_id), None)
        rb_flow2 = next((s for s in read_back_streams if s.get("id") == flow2_id), None)

        if not rb_flow1 or not rb_flow2:
            op.status = "failed"
            op.error_message = (
                f"Read-back failed: expected streams {flow1_id} and {flow2_id} not found"
            )
            self.db.commit()
            raise KeitaroApiError(op.error_message)

        # Verify Flow 1 settings: redirect to google, country filter accept
        flow1_valid = (
            rb_flow1.get("type") == "regular"
            and rb_flow1.get("action_payload") == "https://google.com/"
        )
        # Verify Flow 2 settings: default fallback, offer 100%
        flow2_offers = rb_flow2.get("offers") or []
        target_offer = next(
            (o for o in flow2_offers if o.get("offer_id") == request.offer_id), None
        )
        flow2_valid = (
            rb_flow2.get("type") == "default"
            and target_offer is not None
            and target_offer.get("share") == 100
        )

        if not flow1_valid or not flow2_valid:
            op.status = "failed"
            op.error_message = "Read-back verification mismatch on stream routing settings"
            self.db.commit()
            raise KeitaroApiError(op.error_message)

        # Step 7: Build tracking URL with domain and source parameters (F8)
        tracking_url = self.build_tracking_url(domain_url, alias, source_params)
        remote_ids["tracking_url"] = tracking_url
        op.remote_ids = remote_ids

        # Step 8: Persist in local database
        local_campaign = (
            self.db.query(Campaign).filter_by(remote_campaign_id=remote_campaign_id).first()
        )
        if not local_campaign:
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

        local_offer = self.db.query(Offer).filter_by(remote_offer_id=request.offer_id).first()
        if not local_offer:
            local_offer = Offer(
                remote_offer_id=request.offer_id,
                name=offer_name,
                is_available=True,
            )
            self.db.add(local_offer)
            self.db.flush()

        # Local Stream 1
        s1 = (
            self.db.query(Stream)
            .filter_by(campaign_id=local_campaign.id, remote_stream_id=flow1_id)
            .first()
        )
        if not s1:
            s1 = Stream(
                campaign_id=local_campaign.id,
                remote_stream_id=flow1_id,
                name="Flow 1",
                position=1,
                is_managed=False,
                stream_type="regular",
                stream_schema="redirect",
                action_type="http",
                action_payload="https://google.com/",
                sync_status="clean",
                raw_remote_config=flow1_payload,
            )
            self.db.add(s1)

        # Local Stream 2
        s2 = (
            self.db.query(Stream)
            .filter_by(campaign_id=local_campaign.id, remote_stream_id=flow2_id)
            .first()
        )
        if not s2:
            s2 = Stream(
                campaign_id=local_campaign.id,
                remote_stream_id=flow2_id,
                name="Flow 2",
                position=2,
                is_managed=True,
                stream_type="default",
                stream_schema="landings",
                action_type="http",
                sync_status="clean",
                raw_remote_config=flow2_payload,
            )
            self.db.add(s2)
            self.db.flush()

        so = self.db.query(StreamOffer).filter_by(stream_id=s2.id, offer_id=local_offer.id).first()
        if not so:
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
            verification_status="verified",
        )
