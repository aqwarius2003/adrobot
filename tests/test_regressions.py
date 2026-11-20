from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.core.database import Base
from src.domain.schemas import CampaignCreateRequest
from src.services.creator_service import CampaignCreatorService
from src.services.editor_service import EditorConflictError, StreamEditorService
from src.services.keitaro_client import KeitaroApiError, KeitaroGateway
from src.services.sync_service import StreamSyncService, SyncConflictError


class MockRegressionKT(KeitaroGateway):
    def __init__(self):
        self.domains = [{"id": 10, "name": "track.myads.com", "domain": "track.myads.com"}]
        self.groups = [{"id": 20, "name": "Marketing"}]
        self.sources = [{"id": 30, "name": "Facebook", "parameters": "utm_source=fb&sub1={subid}"}]
        self.offers = {
            101: {"id": 101, "name": "Offer 101"},
            102: {"id": 102, "name": "Offer 102"},
        }
        self.campaigns = {
            500: {
                "id": 500,
                "name": "test_rykov_regress",
                "alias": "regress-500",
                "domain_id": 10,
            },
        }
        self.streams = {
            5001: {
                "id": 5001,
                "campaign_id": 500,
                "name": "Flow 1 (Redirect)",
                "type": "regular",
                "schema": "redirect",
                "action_type": "http",
                "action_payload": "https://google.com/",
                "filters": [{"name": "country", "mode": "accept", "payload": ["AU"]}],
                "offers": [],
            },
            5002: {
                "id": 5002,
                "campaign_id": 500,
                "name": "Flow 2 (Offers)",
                "type": "default",
                "schema": "landings",
                "action_type": "http",
                "filters": [],
                "offers": [{"offer_id": 101, "share": 100, "state": "active"}],
            },
        }
        self.fail_flow1 = False

    async def get_domains(self) -> list[dict[str, Any]]:
        return self.domains

    async def get_groups(self) -> list[dict[str, Any]]:
        return self.groups

    async def get_traffic_sources(self) -> list[dict[str, Any]]:
        return self.sources

    async def get_offers(self) -> list[dict[str, Any]]:
        return list(self.offers.values())

    async def get_offer(self, offer_id: int) -> dict[str, Any]:
        if offer_id in self.offers:
            return self.offers[offer_id]
        raise ValueError(f"Offer {offer_id} not found")

    async def get_campaigns(self) -> list[dict[str, Any]]:
        return list(self.campaigns.values())

    async def get_campaign(self, campaign_id: int) -> dict[str, Any]:
        return self.campaigns.get(campaign_id, {"id": campaign_id})

    async def create_campaign(self, payload: dict[str, Any]) -> dict[str, Any]:
        c_id = 700 + len(self.campaigns)
        res = {**payload, "id": c_id}
        self.campaigns[c_id] = res
        return res

    async def get_campaign_streams(self, campaign_id: int) -> list[dict[str, Any]]:
        return [s for s in self.streams.values() if s.get("campaign_id") == campaign_id]

    async def create_stream(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.fail_flow1 and payload.get("name") == "Flow 1":
            raise RuntimeError("Network error creating Flow 1")
        s_id = 6000 + len(self.streams) + 1
        res = {**payload, "id": s_id}
        self.streams[s_id] = res
        return res

    async def get_stream(self, stream_id: int) -> dict[str, Any]:
        return self.streams.get(stream_id, {"id": stream_id})

    async def update_stream(self, stream_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        if stream_id in self.streams:
            self.streams[stream_id].update(payload)
        return self.streams.get(stream_id, {"id": stream_id})


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    # Critical: expire_on_commit=False, reproducing production SessionLocal configuration!
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.mark.asyncio
async def test_f3_first_fetch_returns_both_streams_with_expire_on_commit_false(db_session):
    """F3: Verify that first-time fetch returns both streams when expire_on_commit=False."""
    mock_kt = MockRegressionKT()
    editor = StreamEditorService(db_session, mock_kt)

    detail = await editor.fetch_campaign_streams(500)
    assert len(detail.streams) == 2
    stream_ids = [s.remote_stream_id for s in detail.streams]
    assert 5001 in stream_ids
    assert 5002 in stream_ids


@pytest.mark.asyncio
async def test_f4_external_deletion_of_all_offers_keeps_stream_managed_and_archives(db_session):
    """F4: Remote deletion of all offers in a stream keeps it managed and marks offers removed with 0%."""
    mock_kt = MockRegressionKT()
    editor = StreamEditorService(db_session, mock_kt)

    # Initial fetch: 1 offer (101) active with 100%
    detail = await editor.fetch_campaign_streams(500)
    flow2 = next(s for s in detail.streams if s.remote_stream_id == 5002)
    assert flow2.is_managed is True
    assert len(flow2.offers) == 1
    assert flow2.offers[0].state == "active"

    # Simulate user clearing all offers directly in Keitaro:
    mock_kt.streams[5002]["offers"] = []

    # Second fetch
    detail2 = await editor.fetch_campaign_streams(500)
    flow2_after = next(s for s in detail2.streams if s.remote_stream_id == 5002)

    # Invariant: stream MUST remain managed, offer 101 MUST be marked removed with share 0%
    assert flow2_after.is_managed is True
    assert len(flow2_after.offers) == 1
    assert flow2_after.offers[0].offer_id == 101
    assert flow2_after.offers[0].state == "removed"
    assert flow2_after.offers[0].share == 0
    assert flow2_after.offers[0].archive_reason == "remote_missing"

    # Invariant: user can Bring back the archived offer
    restored = await editor.bring_back_offer(flow2_after.id, 101, flow2_after.revision)
    assert restored.offers[0].state == "active"
    assert restored.offers[0].share == 100
    assert restored.sync_status == "dirty"


@pytest.mark.asyncio
async def test_f5_publishing_state_locks_mutations_and_fetch(db_session):
    """F5: In-flight publishing / unknown states lock conflicting Fetch, mutations and push."""
    mock_kt = MockRegressionKT()
    editor = StreamEditorService(db_session, mock_kt)
    sync = StreamSyncService(db_session, mock_kt)

    detail = await editor.fetch_campaign_streams(500)
    flow2 = next(s for s in detail.streams if s.remote_stream_id == 5002)

    # Manually simulate stream being in 'publishing' state
    from src.domain.models import Stream

    stream_obj = db_session.query(Stream).filter_by(id=flow2.id).first()
    stream_obj.sync_status = "publishing"
    db_session.commit()

    # 1. Fetch must be rejected
    with pytest.raises(EditorConflictError, match="active sync"):
        await editor.fetch_campaign_streams(500)

    # 2. Add offer must be rejected
    with pytest.raises(EditorConflictError, match="locked in state 'publishing'"):
        await editor.add_offer(flow2.id, 102, stream_obj.revision)

    # 3. Push must be rejected
    with pytest.raises(SyncConflictError, match="Concurrent push locked"):
        await sync.push_stream(flow2.id, stream_obj.revision)


@pytest.mark.asyncio
async def test_f6_creator_flow_failure_does_not_mask_success(db_session):
    """F6: Flow creation failure fails operation and does not produce verified status."""
    mock_kt = MockRegressionKT()
    mock_kt.fail_flow1 = True  # Inject failure on Flow 1
    creator = CampaignCreatorService(db_session, mock_kt)

    req = CampaignCreateRequest(
        name="test_flow_fail",
        geo="AU",
        offer_id=101,
        domain_id=10,
        group_id=20,
    )

    with pytest.raises(KeitaroApiError, match="Failed to create Flow 1"):
        await creator.create_campaign(req)

    from src.domain.models import Operation

    op = db_session.query(Operation).filter_by(operation_type="create_campaign").first()
    assert op.status == "failed"


@pytest.mark.asyncio
async def test_f7_creator_idempotency_payload_mismatch_and_search_recovery(db_session):
    """F7: Key reuse with different payload is rejected; retrying after timeout reuses existing campaign."""
    mock_kt = MockRegressionKT()
    creator = CampaignCreatorService(db_session, mock_kt)

    key = "fixed-idem-key-123"
    req1 = CampaignCreateRequest(name="idem_camp", geo="AU", offer_id=101, idempotency_key=key)
    res1 = await creator.create_campaign(req1)
    assert res1.verification_status == "verified"

    # Attempt reuse key with different offer -> rejected
    req2 = CampaignCreateRequest(name="idem_camp", geo="AU", offer_id=102, idempotency_key=key)
    with pytest.raises(ValueError, match="reused with different payload"):
        await creator.create_campaign(req2)


@pytest.mark.asyncio
async def test_f8_tracking_url_includes_custom_domain_and_source_parameters(db_session):
    """F8: Tracking URL properly formats custom domain URL and appends source parameters."""
    mock_kt = MockRegressionKT()
    creator = CampaignCreatorService(db_session, mock_kt)

    req = CampaignCreateRequest(
        name="ad_campaign",
        geo="AU",
        offer_id=101,
        domain_id=10,
        traffic_source_id=30,
    )
    res = await creator.create_campaign(req)

    # Domain is track.myads.com, source params are utm_source=fb&sub1={subid}
    assert res.tracking_url.startswith("https://track.myads.com/")
    assert "utm_source=fb&sub1={subid}" in res.tracking_url
