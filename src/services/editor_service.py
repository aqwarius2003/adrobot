from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from src.domain.models import Campaign, Offer, Stream, StreamOffer
from src.domain.schemas import CampaignDetailDTO, StreamDTO, StreamOfferDTO
from src.domain.weights import (
    OfferWeightState,
    WeightDistributionError,
    calculate_weights,
    compute_stream_fingerprint,
)
from src.services.keitaro_client import KeitaroGateway


class EditorConflictError(Exception):
    """Raised when an operation conflicts with current stream revision or sync state."""

    pass


class EditorValidationError(ValueError):
    """Raised when an invalid operation or invariant violation is requested."""

    pass


class StreamEditorService:
    """Service handling Editor Fetch, Draft mutations, and Cancel operations."""

    def __init__(self, db: Session, kt_client: KeitaroGateway):
        self.db = db
        self.kt_client = kt_client

    def _compute_fingerprint(self, stream_data: dict[str, Any]) -> str:
        """Create deterministic SHA256 hash of stream offers and essential routing fields."""
        return compute_stream_fingerprint(stream_data)

    async def fetch_campaign_streams(self, remote_campaign_id: int) -> CampaignDetailDTO:
        # Check if local campaign exists
        campaign = self.db.query(Campaign).filter_by(remote_campaign_id=remote_campaign_id).first()
        if not campaign:
            # First-time fetch: query campaign from Keitaro
            kt_campaign = await self.kt_client.get_campaign(remote_campaign_id)
            domain_url = None
            if kt_campaign.get("domain_id"):
                try:
                    domains = await self.kt_client.get_domains()
                    for d in domains:
                        if d.get("id") == kt_campaign.get("domain_id"):
                            domain_url = d.get("name") or d.get("domain")
                            break
                except Exception:
                    pass

            campaign = Campaign(
                remote_campaign_id=remote_campaign_id,
                name=kt_campaign.get("name", f"Campaign #{remote_campaign_id}"),
                alias=kt_campaign.get("alias", ""),
                domain_id=kt_campaign.get("domain_id"),
                domain_url=domain_url,
                group_id=str(kt_campaign.get("group_id")) if kt_campaign.get("group_id") else None,
                traffic_source_id=kt_campaign.get("traffic_source_id"),
            )
            self.db.add(campaign)
            self.db.flush()

        # F5: Check for in-flight / dirty / unknown sync states across streams in this campaign
        existing_streams = self.db.query(Stream).filter_by(campaign_id=campaign.id).all()
        for stream in existing_streams:
            if stream.sync_status in ("dirty", "publishing", "verifying", "unknown"):
                raise EditorConflictError(
                    f"Cannot fetch: stream '{stream.name}' has unpublished local changes or active sync (state='{stream.sync_status}'). Push or Cancel first."
                )
        pre_fetch_revisions = {s.id: s.revision for s in existing_streams}

        # 1. Fetch remote streams and collect all needed remote offers into memory BEFORE touching DB
        remote_streams = await self.kt_client.get_campaign_streams(remote_campaign_id)
        now = datetime.now(UTC)

        needed_offer_details: dict[int, dict[str, Any]] = {}
        for r_stream in remote_streams:
            for r_off in r_stream.get("offers") or []:
                off_id = r_off.get("offer_id")
                if off_id and off_id not in needed_offer_details:
                    existing_off = self.db.query(Offer).filter_by(remote_offer_id=off_id).first()
                    if not existing_off:
                        try:
                            needed_offer_details[off_id] = await self.kt_client.get_offer(off_id)
                        except Exception:
                            needed_offer_details[off_id] = {"name": f"Offer #{off_id}"}

        # 2. Enter atomic DB transaction: check and atomically lock revisions on all streams
        self.db.expire_all()

        # Check for any new streams added concurrently
        current_streams = (
            self.db.query(Stream)
            .filter_by(campaign_id=campaign.id)
            .execution_options(populate_existing=True)
            .all()
        )
        if len(current_streams) != len(existing_streams):
            self.db.rollback()
            raise EditorConflictError("Concurrent modification: stream count changed during fetch.")

        # Atomic CAS update on all streams: if any stream was modified concurrently (revision changed or dirty),
        # the SQL UPDATE will return 0 rows affected, guaranteeing atomic rejection.
        for s_id, expected_rev in pre_fetch_revisions.items():
            rowcount = (
                self.db.query(Stream)
                .filter(
                    Stream.id == s_id,
                    Stream.revision == expected_rev,
                    Stream.sync_status == "clean",
                )
                .update({"revision": Stream.revision + 1}, synchronize_session="fetch")
            )
            if rowcount == 0:
                self.db.rollback()
                raise EditorConflictError(
                    f"Cannot apply fetch: stream #{s_id} was modified concurrently during remote fetch."
                )

        campaign.last_sync_at = now
        remote_stream_ids_seen = set()

        try:
            for r_stream in remote_streams:
                r_id = r_stream["id"]
                remote_stream_ids_seen.add(r_id)

                local_stream = (
                    self.db.query(Stream)
                    .filter_by(campaign_id=campaign.id, remote_stream_id=r_id)
                    .execution_options(populate_existing=True)
                    .first()
                )

                r_offers = r_stream.get("offers") or []
                schema = r_stream.get("schema", "redirect")

                # Check if any offer has unsupported/disabled state
                has_disabled_offers = any(
                    r_off.get("state") in ("disabled", "inactive", "paused", "deleted")
                    for r_off in r_offers
                )

                # Determine if stream is managed
                if schema == "redirect" or has_disabled_offers:
                    is_managed = False
                elif local_stream and local_stream.is_managed:
                    is_managed = True
                else:
                    is_managed = bool(r_offers) and schema != "redirect" and not has_disabled_offers

                if not local_stream:
                    local_stream = Stream(
                        campaign_id=campaign.id,
                        remote_stream_id=r_id,
                        name=r_stream.get("name", f"Stream #{r_id}"),
                        position=r_stream.get("position", 1),
                        is_managed=is_managed,
                        stream_type=r_stream.get("type", "regular"),
                        stream_schema=schema,
                        action_type=r_stream.get("action_type"),
                        action_payload=r_stream.get("action_payload"),
                        sync_status="unsupported" if has_disabled_offers else "clean",
                        remote_fingerprint=self._compute_fingerprint(r_stream),
                        raw_remote_config=r_stream,
                        last_sync_at=now,
                        revision=1,
                    )
                    self.db.add(local_stream)
                    self.db.flush()
                else:
                    local_stream.name = r_stream.get("name", local_stream.name)
                    local_stream.position = r_stream.get("position", local_stream.position)
                    local_stream.stream_type = r_stream.get("type", local_stream.stream_type)
                    local_stream.stream_schema = schema
                    local_stream.action_type = r_stream.get("action_type", local_stream.action_type)
                    local_stream.action_payload = r_stream.get(
                        "action_payload", local_stream.action_payload
                    )
                    local_stream.remote_fingerprint = self._compute_fingerprint(r_stream)
                    local_stream.raw_remote_config = r_stream
                    local_stream.last_sync_at = now
                    local_stream.is_managed = is_managed
                    local_stream.sync_status = "unsupported" if has_disabled_offers else "clean"

                if not is_managed:
                    # Opaque stream (e.g. redirect or unsupported states) - archive any existing offers
                    for so in local_stream.stream_offers:
                        so.baseline_state = "removed"
                        so.baseline_share = 0
                        so.draft_state = "removed"
                        so.draft_share = 0
                        so.draft_pinned = False
                        so.baseline_pinned = False
                        so.draft_auto_pinned = False
                    continue

                # Process offers for managed stream
                remote_offer_ids_seen = set()
                for r_off in r_offers:
                    off_id = r_off["offer_id"]
                    remote_offer_ids_seen.add(off_id)
                    share = int(r_off.get("share", 0))

                    # Lookup or create offer using pre-fetched details
                    local_offer = self.db.query(Offer).filter_by(remote_offer_id=off_id).first()
                    if not local_offer:
                        off_data = needed_offer_details.get(off_id, {})
                        off_name = off_data.get("name", f"Offer #{off_id}")
                        local_offer = Offer(
                            remote_offer_id=off_id, name=off_name, is_available=True
                        )
                        self.db.add(local_offer)
                        self.db.flush()

                    # Find or create StreamOffer
                    so = (
                        self.db.query(StreamOffer)
                        .filter_by(stream_id=local_stream.id, offer_id=local_offer.id)
                        .first()
                    )
                    if not so:
                        so = StreamOffer(
                            stream_id=local_stream.id,
                            offer_id=local_offer.id,
                            position=len(local_stream.stream_offers) + 1,
                            baseline_share=share,
                            baseline_state="active",
                            baseline_pinned=False,
                            draft_share=share,
                            draft_state="active",
                            draft_pinned=False,
                            has_been_published=True,
                        )
                        self.db.add(so)
                    else:
                        so.baseline_share = share
                        so.baseline_state = "active"
                        so.draft_share = share
                        so.draft_state = "active"
                        so.draft_auto_pinned = False
                        so.archive_reason = None
                        so.has_been_published = True

                # External deletions check: local offers that disappeared in Keitaro (F4)
                current_stream_offers = (
                    self.db.query(StreamOffer).filter_by(stream_id=local_stream.id).all()
                )
                for so in current_stream_offers:
                    if so.offer.remote_offer_id not in remote_offer_ids_seen:
                        so.baseline_state = "removed"
                        so.baseline_share = 0
                        so.draft_state = "removed"
                        so.draft_share = 0
                        so.draft_pinned = False
                        so.baseline_pinned = False
                        so.draft_auto_pinned = False
                        so.archive_reason = "remote_missing"

            # Check for streams deleted remotely in KT
            for stream in self.db.query(Stream).filter_by(campaign_id=campaign.id).all():
                if stream.remote_stream_id not in remote_stream_ids_seen:
                    stream.sync_status = "remote_missing"

            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        # F3: Return fresh query of campaign detail
        return self.get_campaign_detail_by_local_id(campaign.id)

    def _build_stream_dto(self, s: Stream) -> StreamDTO:
        stream_offers = (
            self.db.query(StreamOffer)
            .filter_by(stream_id=s.id)
            .order_by(StreamOffer.position, StreamOffer.offer_id)
            .all()
        )
        offer_dtos = []
        for so in stream_offers:
            offer_dtos.append(
                StreamOfferDTO(
                    id=so.id,
                    offer_id=so.offer.remote_offer_id,
                    name=so.offer.name,
                    share=so.draft_share,
                    is_pinned=so.draft_pinned,
                    state=so.draft_state,
                    archive_reason=so.archive_reason,
                    position=so.position,
                )
            )

        remote_camp_id = s.campaign.remote_campaign_id if s.campaign else None
        if remote_camp_id is None and s.campaign_id:
            c = self.db.query(Campaign).filter_by(id=s.campaign_id).first()
            if c:
                remote_camp_id = c.remote_campaign_id

        return StreamDTO(
            id=s.id,
            remote_stream_id=s.remote_stream_id,
            campaign_id=s.campaign_id,
            remote_campaign_id=remote_camp_id,
            name=s.name,
            position=s.position,
            is_managed=s.is_managed,
            stream_type=s.stream_type,
            stream_schema=s.stream_schema,
            action_type=s.action_type,
            action_payload=s.action_payload,
            sync_status=s.sync_status,
            revision=s.revision,
            offers=offer_dtos,
            last_sync_at=s.last_sync_at,
        )

    def _build_campaign_detail_dto(self, campaign: Campaign) -> CampaignDetailDTO:
        base_url = getattr(self.kt_client, "base_url", None) or "https://tracker.example"
        tracking_url = (
            f"{campaign.domain_url.rstrip('/')}/{campaign.alias}"
            if campaign.domain_url
            else f"{base_url.rstrip('/')}/{campaign.alias}"
        )

        streams = (
            self.db.query(Stream).filter_by(campaign_id=campaign.id).order_by(Stream.position).all()
        )
        stream_dtos = [self._build_stream_dto(s) for s in streams]

        return CampaignDetailDTO(
            id=campaign.id,
            remote_campaign_id=campaign.remote_campaign_id,
            name=campaign.name,
            alias=campaign.alias,
            domain_url=campaign.domain_url,
            tracking_url=tracking_url,
            last_sync_at=campaign.last_sync_at,
            streams=stream_dtos,
        )

    def get_campaign_detail_by_remote_id(self, remote_campaign_id: int) -> CampaignDetailDTO:
        campaign = self.db.query(Campaign).filter_by(remote_campaign_id=remote_campaign_id).first()
        if not campaign:
            raise EditorValidationError(f"Campaign with remote ID #{remote_campaign_id} not found")
        return self._build_campaign_detail_dto(campaign)

    def get_campaign_detail_by_local_id(self, local_campaign_id: int) -> CampaignDetailDTO:
        campaign = self.db.query(Campaign).filter_by(id=local_campaign_id).first()
        if not campaign:
            raise EditorValidationError(f"Campaign with local ID #{local_campaign_id} not found")
        return self._build_campaign_detail_dto(campaign)

    def get_campaign_detail(self, campaign_id: int) -> CampaignDetailDTO:
        # Default public method looks up strictly by remote_campaign_id (Keitaro ID)
        return self.get_campaign_detail_by_remote_id(campaign_id)

    def _verify_stream_campaign_ownership(
        self, stream: Stream, expected_remote_campaign_id: int | None
    ) -> None:
        if expected_remote_campaign_id is not None:
            campaign = self.db.query(Campaign).filter_by(id=stream.campaign_id).first()
            if not campaign or campaign.remote_campaign_id != expected_remote_campaign_id:
                raise EditorValidationError(
                    f"Stream #{stream.id} does not belong to expected remote campaign #{expected_remote_campaign_id}"
                )

    def _verify_revision_and_state(self, stream: Stream, expected_revision: int) -> None:
        # F5: Lock modifications during in-flight network states
        if stream.sync_status in ("publishing", "verifying", "unknown"):
            raise EditorConflictError(
                f"Stream is locked in state '{stream.sync_status}'. Modifications blocked until sync completes."
            )

        if stream.revision != expected_revision:
            raise EditorConflictError(
                f"Conflict: stream revision changed (expected {expected_revision}, current {stream.revision}). Reload required."
            )

    async def add_offer(
        self,
        stream_id: int,
        offer_id: int,
        expected_revision: int,
        expected_remote_campaign_id: int | None = None,
    ) -> StreamDTO:
        stream = self.db.query(Stream).filter_by(id=stream_id).first()
        if not stream or not stream.is_managed:
            raise EditorValidationError("Managed stream not found")

        self._verify_stream_campaign_ownership(stream, expected_remote_campaign_id)
        self._verify_revision_and_state(stream, expected_revision)

        existing_so = (
            self.db.query(StreamOffer)
            .join(Offer)
            .filter(StreamOffer.stream_id == stream.id, Offer.remote_offer_id == offer_id)
            .first()
        )

        if existing_so:
            if existing_so.draft_state == "active":
                raise EditorValidationError(f"Offer #{offer_id} is already active in this stream")
            return await self.bring_back_offer(
                stream_id, offer_id, expected_revision, expected_remote_campaign_id
            )

        local_offer = self.db.query(Offer).filter_by(remote_offer_id=offer_id).first()
        if not local_offer:
            try:
                off_data = await self.kt_client.get_offer(offer_id)
                off_name = off_data.get("name", f"Offer #{offer_id}")
            except Exception as exc:
                raise EditorValidationError(
                    f"Offer #{offer_id} not found in Keitaro: {exc}"
                ) from exc
            local_offer = Offer(remote_offer_id=offer_id, name=off_name, is_available=True)
            self.db.add(local_offer)
            self.db.flush()

        new_position = len(stream.stream_offers) + 1
        new_so = StreamOffer(
            stream_id=stream.id,
            offer_id=local_offer.id,
            position=new_position,
            baseline_share=0,
            baseline_state="removed",
            baseline_pinned=False,
            draft_share=0,
            draft_state="active",
            draft_pinned=False,
            has_been_published=False,
        )
        self.db.add(new_so)
        self.db.flush()

        self._recalculate_stream_weights(stream)
        stream.revision += 1
        self._refresh_draft_status(stream)
        self.db.commit()

        return self._build_stream_dto(stream)

    async def remove_offer(
        self,
        stream_id: int,
        offer_id: int,
        expected_revision: int,
        expected_remote_campaign_id: int | None = None,
    ) -> StreamDTO:
        stream = self.db.query(Stream).filter_by(id=stream_id).first()
        if not stream or not stream.is_managed:
            raise EditorValidationError("Managed stream not found")

        self._verify_stream_campaign_ownership(stream, expected_remote_campaign_id)
        self._verify_revision_and_state(stream, expected_revision)

        so = (
            self.db.query(StreamOffer)
            .join(Offer)
            .filter(StreamOffer.stream_id == stream.id, Offer.remote_offer_id == offer_id)
            .first()
        )
        if not so or so.draft_state != "active":
            raise EditorValidationError(f"Active offer #{offer_id} not found in this stream")

        active_count = sum(1 for item in stream.stream_offers if item.draft_state == "active")
        if active_count <= 1:
            raise EditorValidationError("Cannot remove the last active offer in the stream")

        so.draft_state = "removed"
        so.draft_share = 0
        so.draft_pinned = False
        so.draft_auto_pinned = False
        so.archive_reason = "local_remove"

        self._recalculate_stream_weights(stream)
        stream.revision += 1
        self._refresh_draft_status(stream)
        self.db.commit()

        return self._build_stream_dto(stream)

    async def bring_back_offer(
        self,
        stream_id: int,
        offer_id: int,
        expected_revision: int,
        expected_remote_campaign_id: int | None = None,
    ) -> StreamDTO:
        stream = self.db.query(Stream).filter_by(id=stream_id).first()
        if not stream or not stream.is_managed:
            raise EditorValidationError("Managed stream not found")

        self._verify_stream_campaign_ownership(stream, expected_remote_campaign_id)
        self._verify_revision_and_state(stream, expected_revision)

        so = (
            self.db.query(StreamOffer)
            .join(Offer)
            .filter(StreamOffer.stream_id == stream.id, Offer.remote_offer_id == offer_id)
            .first()
        )
        if not so or so.draft_state != "removed":
            raise EditorValidationError(f"Archived offer #{offer_id} not found in this stream")

        # Step 9: Verify the global offer is still available in Keitaro catalog
        try:
            kt_offer = await self.kt_client.get_offer(offer_id)
            if (
                not kt_offer
                or kt_offer.get("state") == "deleted"
                or kt_offer.get("is_available") is False
            ):
                raise EditorValidationError(
                    f"Оффер #{offer_id} недоступен в каталоге Keitaro (удален или заблокирован). Привязка сохранена в архиве."
                )
        except Exception as exc:
            if isinstance(exc, EditorValidationError):
                raise
            raise EditorValidationError(
                f"Оффер #{offer_id} недоступен в каталоге Keitaro: {exc}. Привязка сохранена в архиве."
            ) from exc

        so.draft_state = "active"
        so.draft_pinned = False
        so.draft_auto_pinned = False
        so.archive_reason = None

        self._recalculate_stream_weights(stream)
        stream.revision += 1
        self._refresh_draft_status(stream)
        self.db.commit()

        return self._build_stream_dto(stream)

    async def update_offer_weight(
        self,
        stream_id: int,
        offer_id: int,
        share: int | None,
        is_pinned: bool | None,
        expected_revision: int,
        expected_remote_campaign_id: int | None = None,
    ) -> StreamDTO:
        stream = self.db.query(Stream).filter_by(id=stream_id).first()
        if not stream or not stream.is_managed:
            raise EditorValidationError("Managed stream not found")

        self._verify_stream_campaign_ownership(stream, expected_remote_campaign_id)
        self._verify_revision_and_state(stream, expected_revision)

        so = (
            self.db.query(StreamOffer)
            .join(Offer)
            .filter(StreamOffer.stream_id == stream.id, Offer.remote_offer_id == offer_id)
            .first()
        )
        if not so or so.draft_state != "active":
            raise EditorValidationError(f"Active offer #{offer_id} not found in this stream")

        # Distinguish simple pin-only action from changing a share number
        is_pin_only = (share is None) and (is_pinned is True) and not so.draft_pinned

        if share is not None:
            if share < 0 or share > 100:
                raise EditorValidationError("Share must be an integer between 0 and 100")
            if not so.draft_pinned:
                so.draft_auto_pinned = True
            so.draft_share = share
            so.draft_pinned = True

        if is_pinned is not None:
            so.draft_pinned = is_pinned
            so.draft_auto_pinned = False

        if is_pin_only:
            # SPEC §11.2: Simple pin without new share strictly preserves all existing shares.
            # Only marks the target offer as pinned without rebalancing others.
            pass
        else:
            self._recalculate_stream_weights(stream)

        stream.revision += 1
        self._refresh_draft_status(stream)
        self.db.commit()

        return self._build_stream_dto(stream)

    def cancel_stream(
        self,
        stream_id: int,
        expected_remote_campaign_id: int | None = None,
    ) -> StreamDTO:
        stream = self.db.query(Stream).filter_by(id=stream_id).first()
        if not stream or not stream.is_managed:
            raise EditorValidationError("Managed stream not found")

        self._verify_stream_campaign_ownership(stream, expected_remote_campaign_id)

        if stream.sync_status in ("publishing", "verifying", "unknown"):
            raise EditorConflictError(f"Cannot cancel stream while in '{stream.sync_status}' state")

        for so in list(stream.stream_offers):
            if not so.has_been_published:
                self.db.delete(so)
                if so in stream.stream_offers:
                    stream.stream_offers.remove(so)
            else:
                so.draft_share = so.baseline_share
                so.draft_state = so.baseline_state
                so.draft_pinned = so.baseline_pinned
                so.draft_auto_pinned = False
                so.archive_reason = (
                    None if so.baseline_state == "active" else (so.archive_reason or "local_remove")
                )

        self.db.flush()
        self.db.expire(stream, ["stream_offers"])
        stream.sync_status = "clean"
        stream.revision += 1
        self.db.commit()

        return self._build_stream_dto(stream)

    def _refresh_draft_status(self, stream: Stream) -> None:
        self.db.flush()
        offers = self.db.query(StreamOffer).filter_by(stream_id=stream.id).all()
        # Keep entered weights fixed until ALL weights/composition are restored;
        # releasing pins one by one would redistribute already restored weights.
        # An automatic pin always starts from an unpinned draft, so undo restores
        # False, including an explicit Unpin of a previously published pin.
        if all(
            (so.draft_state, so.draft_share) == (so.baseline_state, so.baseline_share)
            for so in offers
        ):
            for so in offers:
                if so.draft_auto_pinned:
                    so.draft_pinned = False
                    so.draft_auto_pinned = False
        # An edit can undo an earlier edit. Compare values rather than keeping
        # a sticky dirty flag; do not clear unresolved sync failures.
        if stream.sync_status not in ("clean", "dirty"):
            return
        has_changes = any(
            (so.draft_state, so.draft_share, so.draft_pinned)
            != (so.baseline_state, so.baseline_share, so.baseline_pinned)
            for so in offers
        )
        stream.sync_status = "dirty" if has_changes else "clean"

    def _recalculate_stream_weights(self, stream: Stream) -> None:
        self.db.flush()
        stream_offers = self.db.query(StreamOffer).filter_by(stream_id=stream.id).all()
        states = [
            OfferWeightState(
                offer_id=so.id,
                position=so.position,
                share=so.draft_share,
                is_active=(so.draft_state == "active"),
                is_pinned=so.draft_pinned,
            )
            for so in stream_offers
        ]

        try:
            computed = calculate_weights(states)
        except WeightDistributionError as exc:
            raise EditorValidationError(str(exc)) from exc
        computed_dict = {c.offer_id: c for c in computed}

        for so in stream_offers:
            c = computed_dict.get(so.id)
            if c:
                so.draft_share = c.share
                so.draft_state = "active" if c.is_active else "removed"
                so.draft_pinned = c.is_pinned
