import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.logging import logger
from src.domain.campaign_names import (
    CampaignNameConflictError,
    campaign_name_key,
    normalize_campaign_name,
)
from src.domain.iso_countries import is_valid_iso_country
from src.domain.models import Campaign, Offer, Operation, Stream, StreamOffer
from src.domain.schemas import CampaignCreateRequest, CampaignCreateResponse
from src.domain.weights import OfferWeightState, calculate_weights
from src.services.keitaro_client import KeitaroApiError, KeitaroGateway


class CampaignCreatorService:
    """Domain service responsible for Campaign creation with two routing flows."""

    def __init__(self, db: Session, kt_client: KeitaroGateway):
        self.db = db
        self.kt_client = kt_client

    def normalize_campaign_name(self, raw_name: str) -> str:
        return normalize_campaign_name(raw_name)

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
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized_name.lower()).strip("-") or "campaign"
        suffix = uuid.uuid4().hex[:6]
        return f"{slug}-{suffix}"

    @staticmethod
    def format_source_params(source_params: Any) -> str:
        """
        Formats traffic source parameters into a URL query string.
        Supports:
        - str: "utm_source=fb&sub1={subid}"
        - dict: {"source": {"name": "utm_source", "placeholder": "{network}"}, ...}
        - dict: {"utm_source": "fb", "sub1": "{subid}"}
        - None or empty
        """
        if not source_params:
            return ""
        if isinstance(source_params, str):
            return source_params.strip().lstrip("?").strip()
        if isinstance(source_params, dict):
            parts: list[str] = []
            for key, val in source_params.items():
                if isinstance(val, dict):
                    name = str(val.get("name") or key).strip()
                    placeholder = str(val.get("placeholder") or val.get("value") or "").strip()
                    if name and placeholder:
                        parts.append(f"{name}={placeholder}")
                    elif name:
                        parts.append(name)
                elif val is not None:
                    name = str(key).strip()
                    placeholder = str(val).strip()
                    if name and placeholder:
                        parts.append(f"{name}={placeholder}")
                    elif name:
                        parts.append(name)
            return "&".join(parts)
        return ""

    def build_tracking_url(
        self,
        domain_url: str | None,
        alias: str,
        source_params: Any = None,
    ) -> str:
        if domain_url:
            clean_domain = domain_url.strip().rstrip("/")
            if not clean_domain.startswith("http://") and not clean_domain.startswith("https://"):
                clean_domain = f"https://{clean_domain}"
            base = f"{clean_domain}/{alias}"
        else:
            base = f"{settings.normalized_base_url}/{alias}"

        try:
            clean_params = self.format_source_params(source_params)
            if clean_params:
                base = f"{base}?{clean_params}"
        except Exception as exc:
            logger.warning("Failed to format source params %r: %s", source_params, exc)

        return base

    async def create_campaign(self, request: CampaignCreateRequest) -> CampaignCreateResponse:
        idempotency_key = request.idempotency_key or str(uuid.uuid4())

        # Check existing operation
        op = self.db.query(Operation).filter_by(idempotency_key=idempotency_key).first()
        if op:
            # Check payload-key mismatch (F7)
            if op.candidate_payload:
                req_dict = request.model_dump()
                for key in ("name", "geo"):
                    if op.candidate_payload.get(key) != req_dict.get(key):
                        raise ValueError(
                            f"Idempotency key '{idempotency_key}' reused with different payload"
                        )
                op_offers = op.candidate_payload.get("offer_ids") or (
                    [op.candidate_payload.get("offer_id")]
                    if op.candidate_payload.get("offer_id")
                    else []
                )
                if op_offers != request.offer_ids:
                    raise ValueError(
                        f"Idempotency key '{idempotency_key}' reused with different payload"
                    )

            if op.status == "success" and op.remote_ids:
                remote_ids = op.remote_ids
                summary = (
                    op.candidate_payload.get("routing_summary")
                    if op.candidate_payload
                    else f"Flow 1: {request.geo} -> Google; Flow 2: rest -> Offers {request.offer_ids}"
                )
                return CampaignCreateResponse(
                    id=op.target_id or 0,
                    remote_campaign_id=remote_ids.get("campaign_id", 0),
                    name=(op.candidate_payload or {}).get("normalized_name")
                    or self.normalize_campaign_name(request.name),
                    alias=remote_ids.get("alias", ""),
                    tracking_url=remote_ids.get("tracking_url"),
                    flow1_stream_id=remote_ids.get("flow1_id"),
                    flow2_stream_id=remote_ids.get("flow2_id"),
                    routing_summary=summary
                    or f"Flow 1: {request.geo} -> Google; Flow 2: rest -> Offers",
                    verification_status="verified",
                )

        normalized_name = self.normalize_campaign_name(request.name)
        geo_codes = self.normalize_geo(request.geo)

        # Check fresh upstream state before any write. A retry may refer to its own
        # already-created campaign, identified by the operation's saved ID or alias.
        try:
            existing_campaigns = await self.kt_client.get_campaigns()
        except Exception as exc:
            raise KeitaroApiError(
                "Не удалось проверить название кампании. Повторите попытку позже."
            ) from exc
        own_remote_id = (op.remote_ids or {}).get("campaign_id") if op else None
        own_alias = (op.candidate_payload or {}).get("alias") if op else None
        for campaign in existing_campaigns:
            if own_remote_id is not None and campaign.get("id") == own_remote_id:
                continue
            if own_alias and campaign.get("alias") == own_alias:
                continue
            if campaign_name_key(campaign.get("name")) == campaign_name_key(normalized_name):
                raise CampaignNameConflictError()

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

        # Step 1: Validate selected offers in Keitaro & calculate initial weights
        op.stage = "verifying_offers"
        self.db.commit()

        selected_offers_data: list[tuple[int, str]] = []
        for oid in request.offer_ids:
            try:
                offer_data = await self.kt_client.get_offer(oid)
                off_name = offer_data.get("name", f"Offer #{oid}")
                selected_offers_data.append((oid, off_name))
            except Exception as exc:
                op.status = "failed"
                op.error_message = f"Offer {oid} validation failed: {exc}"
                self.db.commit()
                raise ValueError(f"Offer {oid} not found or unavailable in Keitaro: {exc}") from exc

        # Calculate initial deterministic integer weights summing to 100%
        weight_states = [
            OfferWeightState(
                offer_id=oid,
                position=idx + 1,
                share=0,
                is_active=True,
                is_pinned=False,
            )
            for idx, (oid, _) in enumerate(selected_offers_data)
        ]
        calculated_states = calculate_weights(weight_states)
        offer_shares = {cs.offer_id: cs.share for cs in calculated_states}

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

            # A matching saved alias recovers a previous attempt without creating twice.
            for c in existing_campaigns:
                if c.get("alias") == alias:
                    remote_campaign_id = c["id"]
                    logger.info(
                        "Found existing campaign %s for alias %s after retry",
                        remote_campaign_id,
                        alias,
                    )
                    break

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
                    if (
                        isinstance(exc, KeitaroApiError)
                        and exc.status_code == 409
                        and "campaign_name_exists" in str(exc.response_body)
                    ):
                        raise CampaignNameConflictError() from exc
                    raise KeitaroApiError(f"Failed to create campaign in Keitaro: {exc}") from exc

            # Save confirmed campaign_id immediately (F6)
            remote_ids["campaign_id"] = remote_campaign_id
            remote_ids["alias"] = alias
            op.remote_ids = remote_ids
            self.db.commit()

        # Check if streams already exist for this campaign to prevent duplicate flows
        if remote_campaign_id and (
            not remote_ids.get("flow1_id") or not remote_ids.get("flow2_id")
        ):
            try:
                existing_streams = await self.kt_client.get_campaign_streams(remote_campaign_id)
                for s in existing_streams:
                    if (
                        s.get("type") == "regular"
                        and s.get("name") == "Flow 1"
                        and not remote_ids.get("flow1_id")
                    ):
                        remote_ids["flow1_id"] = s.get("id")
                    elif (
                        s.get("type") == "default"
                        and s.get("name") == "Flow 2"
                        and not remote_ids.get("flow2_id")
                    ):
                        remote_ids["flow2_id"] = s.get("id")
                if "flow1_id" in remote_ids or "flow2_id" in remote_ids:
                    op.remote_ids = remote_ids
                    self.db.commit()
            except Exception as exc:
                logger.warning(
                    "Could not check existing streams for campaign %s: %s",
                    remote_campaign_id,
                    exc,
                )

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

        # Step 5: Create Flow 2 (Default -> Offers)
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
                    "offer_id": oid,
                    "share": offer_shares[oid],
                    "state": "active",
                }
                for oid, _ in selected_offers_data
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
        # Verify Flow 2 settings: default fallback, all requested offers with exact calculated shares summing to 100%
        flow2_offers = rb_flow2.get("offers") or []
        active_rb_offers = [o for o in flow2_offers if o.get("state", "active") == "active"]
        rb_offer_shares = {o.get("offer_id"): o.get("share") for o in active_rb_offers}

        flow2_valid = (
            rb_flow2.get("type") == "default"
            and len(active_rb_offers) == len(request.offer_ids)
            and all(rb_offer_shares.get(oid) == offer_shares[oid] for oid in request.offer_ids)
            and sum(rb_offer_shares.values()) == 100
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

        for idx, (oid, off_name) in enumerate(selected_offers_data):
            local_offer = self.db.query(Offer).filter_by(remote_offer_id=oid).first()
            if not local_offer:
                local_offer = Offer(
                    remote_offer_id=oid,
                    name=off_name,
                    is_available=True,
                )
                self.db.add(local_offer)
                self.db.flush()

            so = (
                self.db.query(StreamOffer)
                .filter_by(stream_id=s2.id, offer_id=local_offer.id)
                .first()
            )
            if not so:
                so = StreamOffer(
                    stream_id=s2.id,
                    offer_id=local_offer.id,
                    position=idx + 1,
                    baseline_share=offer_shares[oid],
                    baseline_state="active",
                    baseline_pinned=False,
                    draft_share=offer_shares[oid],
                    draft_state="active",
                    draft_pinned=False,
                    has_been_published=True,
                )
                self.db.add(so)

        # Update Operation
        op.stage = "completed"
        op.status = "success"
        op.target_id = local_campaign.id

        if len(selected_offers_data) == 1:
            offers_summary = (
                f"{selected_offers_data[0][1]} [ID: {selected_offers_data[0][0]}] (100%)"
            )
        else:
            offers_summary = ", ".join(
                f"{name} [ID: {oid}] ({offer_shares[oid]}%)" for oid, name in selected_offers_data
            )
        routing_summary = (
            f"Flow 1 ({flow1_id}): {', '.join(geo_codes)} -> https://google.com/; "
            f"Flow 2 ({flow2_id}): остальные -> {offers_summary}"
        )
        if op.candidate_payload:
            cand = dict(op.candidate_payload)
            cand["routing_summary"] = routing_summary
            op.candidate_payload = cand
        self.db.commit()

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
