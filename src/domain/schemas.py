from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

# ==================== Options & Lookup Schemas ====================


class LookupItem(BaseModel):
    id: int
    name: str


class LookupOptionsResponse(BaseModel):
    domains: list[LookupItem] = Field(default_factory=list)
    groups: list[LookupItem] = Field(default_factory=list)
    traffic_sources: list[LookupItem] = Field(default_factory=list)


class CountryCatalogItem(BaseModel):
    code: str
    name_ru: str
    name_en: str
    aliases: list[str] = Field(default_factory=list)


class OfferSearchItem(BaseModel):
    id: int
    name: str
    is_available: bool = True


# ==================== Creator Schemas ====================


class CampaignCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Campaign name (without or with prefix)")
    geo: str = Field(..., min_length=2, description="Country code(s), e.g. 'AU' or 'MX,AU,RO'")
    offer_id: int | None = Field(
        default=None, description="Primary or single selected existing offer ID"
    )
    offer_ids: list[int] = Field(
        default_factory=list, description="Selected existing offer IDs (1 or more)"
    )
    domain_id: int | None = None
    group_id: str | int | None = None
    traffic_source_id: int | None = None
    idempotency_key: str | None = None

    @model_validator(mode="after")
    def validate_offers(self) -> "CampaignCreateRequest":
        ids = list(self.offer_ids)
        if self.offer_id and self.offer_id not in ids:
            ids.insert(0, self.offer_id)
        if not ids:
            raise ValueError("At least one target offer must be selected")
        self.offer_ids = ids
        self.offer_id = ids[0]
        return self


class CampaignCreateResponse(BaseModel):
    id: int
    remote_campaign_id: int
    name: str
    alias: str
    tracking_url: str | None = None
    flow1_stream_id: int | None = None
    flow2_stream_id: int | None = None
    routing_summary: str
    verification_status: str


# ==================== Editor Schemas ====================


class StreamOfferDTO(BaseModel):
    id: int
    offer_id: int
    name: str
    share: int
    is_pinned: bool
    state: str  # "active" or "removed"
    archive_reason: str | None = None
    position: int


class StreamDTO(BaseModel):
    id: int
    remote_stream_id: int
    campaign_id: int | None = None
    remote_campaign_id: int | None = None
    name: str
    position: int
    is_managed: bool
    stream_type: str
    stream_schema: str
    action_type: str | None = None
    action_payload: str | None = None
    sync_status: str  # clean, dirty, publishing, verifying, conflict, unknown, remote_missing
    revision: int
    offers: list[StreamOfferDTO] = Field(default_factory=list)
    last_sync_at: datetime | None = None


class CampaignSummaryDTO(BaseModel):
    id: int
    remote_campaign_id: int
    name: str
    alias: str
    last_sync_at: datetime | None = None


class CampaignDetailDTO(BaseModel):
    id: int
    remote_campaign_id: int
    name: str
    alias: str
    domain_url: str | None = None
    tracking_url: str | None = None
    last_sync_at: datetime | None = None
    streams: list[StreamDTO] = Field(default_factory=list)


# ==================== Mutation Schemas ====================


class AddOfferRequest(BaseModel):
    offer_id: int
    expected_revision: int
    expected_remote_campaign_id: int | None = None


class OfferMutationRequest(BaseModel):
    expected_revision: int
    expected_remote_campaign_id: int | None = None


class UpdateOfferWeightRequest(BaseModel):
    share: int | None = None
    is_pinned: bool | None = None
    expected_revision: int
    expected_remote_campaign_id: int | None = None


class CancelStreamRequest(BaseModel):
    expected_remote_campaign_id: int | None = None


class PushRequest(BaseModel):
    expected_revision: int
    idempotency_key: str | None = None
    expected_remote_campaign_id: int | None = None


class OperationStatusResponse(BaseModel):
    id: int
    idempotency_key: str
    operation_type: str
    status: str
    stage: str
    error_message: str | None = None
    remote_ids: dict[str, Any] | None = None
