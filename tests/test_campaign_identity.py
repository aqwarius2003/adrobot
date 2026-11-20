import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.dependencies import get_db, get_keitaro_client
from src.core.database import Base
from src.domain.models import Campaign, Offer, Stream, StreamOffer
from src.main import create_app
from src.services.keitaro_client import KeitaroGateway

TEST_DB_URL = "sqlite:///:memory:"
engine = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


class MockKeitaroForIdentity(KeitaroGateway):
    def __init__(self):
        self.campaigns = {
            2: {"id": 2, "name": "test_rykov_02", "alias": "rykov-02"},
            3: {"id": 3, "name": "test_rykov_03", "alias": "rykov-03"},
        }
        self.streams = {
            12: {
                "id": 12,
                "campaign_id": 2,
                "name": "Flow 1",
                "type": "regular",
                "schema": "redirect",
                "offers": [],
            },
            13: {
                "id": 13,
                "campaign_id": 2,
                "name": "Flow 2",
                "type": "default",
                "schema": "landings",
                "offers": [{"offer_id": 101, "share": 100, "state": "active"}],
            },
            16: {
                "id": 16,
                "campaign_id": 3,
                "name": "Flow 1",
                "type": "regular",
                "schema": "redirect",
                "offers": [],
            },
            17: {
                "id": 17,
                "campaign_id": 3,
                "name": "Flow 2",
                "type": "default",
                "schema": "landings",
                "offers": [
                    {"offer_id": 103, "share": 34, "state": "active"},
                    {"offer_id": 105, "share": 33, "state": "active"},
                    {"offer_id": 101, "share": 33, "state": "active"},
                ],
            },
        }

    async def get_campaigns(self):
        return list(self.campaigns.values())

    async def get_campaign(self, campaign_id: int):
        return self.campaigns.get(campaign_id, {"id": campaign_id})

    async def get_campaign_streams(self, campaign_id: int):
        return [s for s in self.streams.values() if s.get("campaign_id") == campaign_id]

    async def get_stream(self, stream_id: int):
        return self.streams.get(stream_id, {"id": stream_id})

    async def create_campaign(self, payload: dict):
        new_id = max(self.campaigns.keys(), default=0) + 1
        item = {"id": new_id, **payload}
        self.campaigns[new_id] = item
        return item

    async def create_stream(self, payload: dict):
        new_id = max(self.streams.keys(), default=0) + 1
        item = {"id": new_id, **payload}
        self.streams[new_id] = item
        return item

    async def get_domains(self):
        return [{"id": 1, "name": "track.example", "domain": "track.example"}]

    async def get_groups(self):
        return [{"id": 1, "name": "Default Group"}]

    async def get_traffic_sources(self):
        return [{"id": 1, "name": "Google", "parameters": {}}]

    async def update_stream(self, stream_id: int, payload: dict):
        if stream_id in self.streams:
            self.streams[stream_id].update(payload)
        return self.streams[stream_id]

    async def get_offers(self):
        return [
            {"id": 101, "name": "Offer 101"},
            {"id": 102, "name": "Offer 102"},
            {"id": 103, "name": "Offer 103"},
            {"id": 105, "name": "Offer 105"},
        ]

    async def get_offer(self, offer_id: int):
        return {"id": offer_id, "name": f"Offer {offer_id}"}


@pytest.fixture(autouse=True)
def init_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def mock_kt():
    return MockKeitaroForIdentity()


@pytest.fixture
def client(mock_kt):
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_keitaro_client] = lambda: mock_kt
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def seed_overlapping_campaigns(db):
    """Seed DB with intersecting local PKs and remote Keitaro IDs:

    Local Campaign 1 -> remote_campaign_id=2 (has streams 12, 13)
    Local Campaign 2 -> remote_campaign_id=3 (has streams 16, 17)
    Notice: Campaign 2's local id=2 is identical to Campaign 1's remote_campaign_id=2!
    """
    c1 = Campaign(id=1, remote_campaign_id=2, name="test_rykov_02", alias="rykov-02")
    c2 = Campaign(id=2, remote_campaign_id=3, name="test_rykov_03", alias="rykov-03")
    db.add_all([c1, c2])
    db.flush()

    s12 = Stream(
        id=1,
        campaign_id=c1.id,
        remote_stream_id=12,
        name="Flow 1",
        position=1,
        is_managed=False,
    )
    s13 = Stream(
        id=2,
        campaign_id=c1.id,
        remote_stream_id=13,
        name="Flow 2",
        position=2,
        is_managed=True,
    )
    s16 = Stream(
        id=3,
        campaign_id=c2.id,
        remote_stream_id=16,
        name="Flow 1",
        position=1,
        is_managed=False,
    )
    s17 = Stream(
        id=4,
        campaign_id=c2.id,
        remote_stream_id=17,
        name="Flow 2",
        position=2,
        is_managed=True,
    )
    db.add_all([s12, s13, s16, s17])
    db.flush()

    o101 = Offer(id=1, remote_offer_id=101, name="Offer 101")
    o103 = Offer(id=2, remote_offer_id=103, name="Offer 103")
    o105 = Offer(id=3, remote_offer_id=105, name="Offer 105")
    db.add_all([o101, o103, o105])
    db.flush()

    # Stream 13 (Campaign 2) has offer 101
    so13 = StreamOffer(
        stream_id=s13.id,
        offer_id=o101.id,
        position=1,
        draft_share=100,
        baseline_share=100,
        draft_state="active",
        baseline_state="active",
    )
    # Stream 17 (Campaign 3) has offers 103, 105, 101
    so17_1 = StreamOffer(
        stream_id=s17.id,
        offer_id=o103.id,
        position=1,
        draft_share=34,
        baseline_share=34,
        draft_state="active",
        baseline_state="active",
    )
    so17_2 = StreamOffer(
        stream_id=s17.id,
        offer_id=o105.id,
        position=2,
        draft_share=33,
        baseline_share=33,
        draft_state="active",
        baseline_state="active",
    )
    so17_3 = StreamOffer(
        stream_id=s17.id,
        offer_id=o101.id,
        position=3,
        draft_share=33,
        baseline_share=33,
        draft_state="active",
        baseline_state="active",
    )
    db.add_all([so13, so17_1, so17_2, so17_3])
    db.commit()


def test_reproduction_campaign_swap_on_fetch(db_session, client: TestClient):
    """Reproduces BROWSER_REVIEW_WRONG_CAMPAIGN.md:

    When campaign_id 3 is fetched, it MUST NOT return Campaign 2 (streams 12, 13).
    It MUST return Campaign 3 (streams 16, 17) and remote_campaign_id == 3.
    """
    seed_overlapping_campaigns(db_session)

    # 1. Direct GET /api/campaigns/3 should return campaign 3
    res_get = client.get("/api/campaigns/3")
    assert res_get.status_code == 200
    data_get = res_get.json()
    assert data_get["remote_campaign_id"] == 3
    stream_remote_ids = [s["remote_stream_id"] for s in data_get["streams"]]
    assert stream_remote_ids == [16, 17]

    # 2. Fetch streams for campaign 3
    res_fetch = client.post("/api/campaigns/3/fetch-streams")
    assert res_fetch.status_code == 200
    data_fetch = res_fetch.json()

    # CRITICAL CHECK: Before fix, this returned remote_campaign_id=2 and streams [12, 13]!
    assert data_fetch["remote_campaign_id"] == 3
    assert data_fetch["name"] == "test_rykov_03"
    fetch_stream_remote_ids = [s["remote_stream_id"] for s in data_fetch["streams"]]
    assert fetch_stream_remote_ids == [16, 17], (
        f"Expected [16, 17] but got {fetch_stream_remote_ids}"
    )


def test_mutation_campaign_ownership_check(db_session, client: TestClient):
    """Verifies that mutations validate expected_remote_campaign_id against stream's campaign."""
    seed_overlapping_campaigns(db_session)

    # Stream with id=4 belongs to Campaign 3 (remote_campaign_id=3)
    # Attempting to mutate stream 4 with expected_remote_campaign_id=2 must fail
    res = client.patch(
        "/api/streams/4/offers/103",
        json={
            "share": 50,
            "expected_revision": 1,
            "expected_remote_campaign_id": 2,  # wrong campaign!
        },
    )
    assert res.status_code == 422
    assert "belong to expected" in res.json()["detail"].lower()

    # Passing matching expected_remote_campaign_id=3 succeeds
    res_ok = client.patch(
        "/api/streams/4/offers/103",
        json={
            "share": 50,
            "expected_revision": 1,
            "expected_remote_campaign_id": 3,
        },
    )
    assert res_ok.status_code == 200
    updated = res_ok.json()
    assert updated["remote_campaign_id"] == 3


def test_cancel_and_push_campaign_ownership_check(db_session, client: TestClient):
    """Verifies that cancel and push operations enforce campaign ownership."""
    seed_overlapping_campaigns(db_session)

    # Cancel with mismatched expected_remote_campaign_id must fail
    res_cancel_fail = client.post(
        "/api/streams/4/cancel",
        json={"expected_remote_campaign_id": 2},
    )
    assert res_cancel_fail.status_code == 422
    assert "belong to expected" in res_cancel_fail.json()["detail"].lower()

    # Cancel with correct expected_remote_campaign_id succeeds
    res_cancel_ok = client.post(
        "/api/streams/4/cancel",
        json={"expected_remote_campaign_id": 3},
    )
    assert res_cancel_ok.status_code == 200

    # Push with mismatched expected_remote_campaign_id must fail with 409 Conflict
    res_push_fail = client.post(
        "/api/streams/4/push",
        json={
            "expected_revision": 1,
            "expected_remote_campaign_id": 2,
        },
    )
    assert res_push_fail.status_code == 409
    assert "belong to expected" in res_push_fail.json()["detail"].lower()


@pytest.mark.asyncio
async def test_creator_does_not_duplicate_streams_on_retry(db_session, mock_kt):
    """Verifies that creator does not create duplicate streams when campaign already exists."""
    from src.domain.schemas import CampaignCreateRequest
    from src.services.creator_service import CampaignCreatorService

    creator = CampaignCreatorService(db_session, mock_kt)
    req = CampaignCreateRequest(
        name="retry_campaign",
        geo="KZ",
        offer_ids=[101],
        idempotency_key="key-retry-1",
    )

    # Initial creation
    resp1 = await creator.create_campaign(req)
    assert resp1.remote_campaign_id is not None
    cid = resp1.remote_campaign_id

    # Count streams in mock
    streams_initial = await mock_kt.get_campaign_streams(cid)
    assert len(streams_initial) == 2

    # Simulate a second request with same idempotency key
    resp2 = await creator.create_campaign(req)
    assert resp2.remote_campaign_id == cid

    # Check streams count: MUST remain 2 (NO DUPLICATES)
    streams_after = await mock_kt.get_campaign_streams(cid)
    assert len(streams_after) == 2
