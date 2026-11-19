from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# ==================== Options & Lookup Schemas ====================


class LookupItem(BaseModel):
    id: int
    name: str


class LookupOptionsResponse(BaseModel):
    domains: list[LookupItem] = Field(default_factory=list)
    groups: list[LookupItem] = Field(default_factory=list)
    traffic_sources: list[LookupItem] = Field(default_factory=list)


class OfferSearchItem(BaseModel):
    id: int
    name: str
    is_available: bool = True


# ==================== Creator Schemas ====================


class CampaignCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Campaign name (without or with prefix)")
    geo: str = Field(..., min_length=2, description="Country code(s), e.g. 'AU' or 'MX,AU,RO'")
    offer_id: int = Field(..., gt=0, description="Selected existing offer ID")
    domain_id: int | None = None
    group_id: str | int | None = None
    traffic_source_id: int | None = None
    idempotency_key: str | None = None


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


class OfferMutationRequest(BaseModel):
    expected_revision: int


class UpdateOfferWeightRequest(BaseModel):
    share: int | None = None
    is_pinned: bool | None = None
    expected_revision: int


class PushRequest(BaseModel):
    expected_revision: int
    idempotency_key: str | None = None


class OperationStatusResponse(BaseModel):
    id: int
    idempotency_key: str
    operation_type: str
    status: str
    stage: str
    error_message: str | None = None
    remote_ids: dict[str, Any] | None = None
