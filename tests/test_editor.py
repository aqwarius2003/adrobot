from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.dependencies import get_db, get_keitaro_client
from src.core.database import Base
from src.main import create_app
from src.services.keitaro_client import KeitaroGateway

# In-memory database for testing
TEST_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    TEST_DATABASE_URL,
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


class MockKeitaroEditorClient(KeitaroGateway):
    def __init__(self):
        self.base_url = "https://tracker.example"
        self.campaigns = {
            100: {"id": 100, "name": "test_rykov_demo", "alias": "demo-camp"},
        }
        self.streams = {
            1001: {
                "id": 1001,
                "campaign_id": 100,
                "name": "Flow 1 (Redirect)",
                "type": "regular",
                "schema": "redirect",
                "action_type": "http",
                "action_payload": "https://google.com/",
                "offers": [],
            },
            1002: {
                "id": 1002,
                "campaign_id": 100,
                "name": "Flow 2 (Offers)",
                "type": "default",
                "schema": "landings",
                "action_type": "http",
                "offers": [
                    {"offer_id": 501, "share": 34, "state": "active"},
                    {"offer_id": 502, "share": 33, "state": "active"},
                    {"offer_id": 503, "share": 33, "state": "active"},
                ],
            },
        }
        self.offers = {
            501: {"id": 501, "name": "Offer Alpha"},
            502: {"id": 502, "name": "Offer Beta"},
            503: {"id": 503, "name": "Offer Gamma"},
            504: {"id": 504, "name": "Offer Delta"},
        }
        self.last_update_payload = None

    async def get_domains(self) -> list[dict[str, Any]]:
        return []

    async def get_groups(self) -> list[dict[str, Any]]:
        return []

    async def get_traffic_sources(self) -> list[dict[str, Any]]:
        return []

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
        return {"id": 999}

    async def get_campaign_streams(self, campaign_id: int) -> list[dict[str, Any]]:
        return [s for s in self.streams.values() if s.get("campaign_id") == campaign_id]

    async def create_stream(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"id": 888}

    async def get_stream(self, stream_id: int) -> dict[str, Any]:
        return self.streams.get(stream_id, {"id": stream_id})

    async def update_stream(self, stream_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self.last_update_payload = payload
        if stream_id in self.streams:
            self.streams[stream_id]["offers"] = payload.get("offers", [])
        return self.streams.get(stream_id, {"id": stream_id})


@pytest.fixture
def mock_kt():
    return MockKeitaroEditorClient()


@pytest.fixture(autouse=True)
def setup_test_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(mock_kt):
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_keitaro_client] = lambda: mock_kt
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_full_video_editor_scenario(client: TestClient, mock_kt: MockKeitaroEditorClient):
    """
    Simulates the exact end-to-end scenario from video (SPEC §16.2):
    1. Fetch streams: Flow 1 is opaque (redirect), Flow 2 has 3 offers (34, 33, 33).
    2. Add 4th offer (504): weights become 25/25/25/25, status becomes dirty.
    3. Cancel: restores draft to baseline (34, 33, 33), clean.
    4. Add 4th offer again -> Push to KT: verified 25/25/25/25 in KT, clean.
    5. Remove 1 offer -> 0% removed, dirty. Push to KT: KT payload does NOT contain removed offer!
    6. Bring back offer -> active, dirty. Push to KT: KT receives the returned offer.
    7. External deletion in KT + Fetch -> offer marked Removed locally with 0%!
    8. Pin offer 1 at 25% -> Bring back -> offer 1 stays 25%, others divide remainder to 100%!
    """
    # 1. Fetch Streams
    res = client.post("/api/campaigns/100/fetch-streams")
    assert res.status_code == 200
    camp_data = res.json()
    assert len(camp_data["streams"]) == 2

    flow1 = next(s for s in camp_data["streams"] if s["remote_stream_id"] == 1001)
    flow2 = next(s for s in camp_data["streams"] if s["remote_stream_id"] == 1002)

    assert flow1["is_managed"] is False
    assert flow2["is_managed"] is True
    assert len(flow2["offers"]) == 3
    assert flow2["sync_status"] == "clean"

    # 2. Add 4th offer (504) -> 25/25/25/25, dirty
    res = client.post(
        f"/api/streams/{flow2['id']}/offers",
        json={"offer_id": 504, "expected_revision": flow2["revision"]},
    )
    assert res.status_code == 200
    flow2 = res.json()
    assert flow2["sync_status"] == "dirty"
    assert len(flow2["offers"]) == 4
    assert [o["share"] for o in flow2["offers"]] == [25, 25, 25, 25]
    assert sum(o["share"] for o in flow2["offers"]) == 100

    # 3. Cancel -> restores baseline
    res = client.post(f"/api/streams/{flow2['id']}/cancel")
    assert res.status_code == 200
    flow2 = res.json()
    assert flow2["sync_status"] == "clean"
    assert len(flow2["offers"]) == 3
    assert [o["share"] for o in flow2["offers"]] == [34, 33, 33]

    # 4. Add 4th offer again -> Push to KT
    res = client.post(
        f"/api/streams/{flow2['id']}/offers",
        json={"offer_id": 504, "expected_revision": flow2["revision"]},
    )
    assert res.status_code == 200
    flow2 = res.json()
    assert [o["share"] for o in flow2["offers"]] == [25, 25, 25, 25]

    res = client.post(
        f"/api/streams/{flow2['id']}/push",
        json={"expected_revision": flow2["revision"]},
    )
    assert res.status_code == 200
    flow2 = res.json()
    assert flow2["sync_status"] == "clean"
    # Verify KT received all 4 offers with share 25
    assert len(mock_kt.last_update_payload["offers"]) == 4
    for o in mock_kt.last_update_payload["offers"]:
        assert o["share"] == 25
        assert o["state"] == "active"
        assert "is_pinned" not in o  # Pins must not be sent to KT!

    # 5. Remove Offer 504 -> share=0, removed -> Push to KT
    res = client.post(
        f"/api/streams/{flow2['id']}/offers/504/remove",
        json={"expected_revision": flow2["revision"]},
    )
    assert res.status_code == 200
    flow2 = res.json()
    assert flow2["sync_status"] == "dirty"
    off504 = next(o for o in flow2["offers"] if o["offer_id"] == 504)
    assert off504["state"] == "removed"
    assert off504["share"] == 0

    # Push to KT
    res = client.post(
        f"/api/streams/{flow2['id']}/push",
        json={"expected_revision": flow2["revision"]},
    )
    assert res.status_code == 200
    flow2 = res.json()
    assert flow2["sync_status"] == "clean"
    # In KT, offer 504 MUST NOT be in the offers payload!
    assert len(mock_kt.last_update_payload["offers"]) == 3
    assert 504 not in [o["offer_id"] for o in mock_kt.last_update_payload["offers"]]

    # 6. Bring back offer 504 -> active -> Push to KT
    res = client.post(
        f"/api/streams/{flow2['id']}/offers/504/bring-back",
        json={"expected_revision": flow2["revision"]},
    )
    assert res.status_code == 200
    flow2 = res.json()
    assert flow2["sync_status"] == "dirty"
    off504 = next(o for o in flow2["offers"] if o["offer_id"] == 504)
    assert off504["state"] == "active"
    assert off504["share"] == 25

    res = client.post(
        f"/api/streams/{flow2['id']}/push",
        json={"expected_revision": flow2["revision"]},
    )
    assert res.status_code == 200
    flow2 = res.json()
    assert flow2["sync_status"] == "clean"

    # 7. External deletion in KT + Fetch -> offer marked Removed with 0%
    # Simulate user deleting offer 503 directly in Keitaro:
    mock_kt.streams[1002]["offers"] = [
        o for o in mock_kt.streams[1002]["offers"] if o["offer_id"] != 503
    ]
    res = client.post("/api/campaigns/100/fetch-streams")
    assert res.status_code == 200
    camp_data = res.json()
    flow2 = next(s for s in camp_data["streams"] if s["remote_stream_id"] == 1002)

    # Offer 503 is now locally Removed with 0% and Bring back available
    off503 = next(o for o in flow2["offers"] if o["offer_id"] == 503)
    assert off503["state"] == "removed"
    assert off503["share"] == 0
    assert flow2["sync_status"] == "clean"

    # 8. Pin offer 501 at 25% -> Bring back 503
    res = client.patch(
        f"/api/streams/{flow2['id']}/offers/501",
        json={"share": 25, "is_pinned": True, "expected_revision": flow2["revision"]},
    )
    assert res.status_code == 200
    flow2 = res.json()
    off501 = next(o for o in flow2["offers"] if o["offer_id"] == 501)
    assert off501["share"] == 25
    assert off501["is_pinned"] is True

    # Bring back 503
    res = client.post(
        f"/api/streams/{flow2['id']}/offers/503/bring-back",
        json={"expected_revision": flow2["revision"]},
    )
    assert res.status_code == 200
    flow2 = res.json()
    # 501 stays 25! Remaining 75 distributed among 3 unpinned (502, 504, 503) -> 25 each!
    off501 = next(o for o in flow2["offers"] if o["offer_id"] == 501)
    assert off501["share"] == 25
    assert off501["is_pinned"] is True
    assert sum(o["share"] for o in flow2["offers"] if o["state"] == "active") == 100


def test_single_offer_invalid_weight_is_validation_error(client: TestClient, mock_kt):
    mock_kt.streams[1002]["offers"] = [{"offer_id": 501, "share": 100, "state": "active"}]
    campaign = client.post("/api/campaigns/100/fetch-streams").json()
    stream = next(s for s in campaign["streams"] if s["remote_stream_id"] == 1002)
    response = client.patch(
        f"/api/streams/{stream['id']}/offers/501",
        json={"share": 87, "is_pinned": True, "expected_revision": stream["revision"]},
    )
    assert response.status_code == 422
    assert "100%" in response.json()["detail"]
    saved = client.get("/api/campaigns/100").json()
    saved_stream = next(s for s in saved["streams"] if s["id"] == stream["id"])
    assert saved_stream["revision"] == stream["revision"]
    assert saved_stream["sync_status"] == "clean"
    assert saved_stream["offers"][0]["share"] == 100
    assert saved_stream["offers"][0]["is_pinned"] is False
    assert mock_kt.last_update_payload is None


def test_dirty_fetch_rejected(client: TestClient):
    # Fetch clean
    res = client.post("/api/campaigns/100/fetch-streams")
    assert res.status_code == 200
    camp_data = res.json()
    flow2 = next(s for s in camp_data["streams"] if s["remote_stream_id"] == 1002)

    # Make dirty
    res = client.post(
        f"/api/streams/{flow2['id']}/offers",
        json={"offer_id": 504, "expected_revision": flow2["revision"]},
    )
    assert res.status_code == 200

    # Attempt fetch while dirty -> must be rejected with 409
    res = client.post("/api/campaigns/100/fetch-streams")
    assert res.status_code == 409
    assert "unpublished local changes" in res.json()["detail"]


def test_stale_revision_rejected(client: TestClient):
    # Fetch clean
    res = client.post("/api/campaigns/100/fetch-streams")
    assert res.status_code == 200
    camp_data = res.json()
    flow2 = next(s for s in camp_data["streams"] if s["remote_stream_id"] == 1002)

    # Pass outdated revision (e.g. 999)
    res = client.post(
        f"/api/streams/{flow2['id']}/offers",
        json={"offer_id": 504, "expected_revision": 999},
    )
    assert res.status_code == 409
    assert "Conflict: stream revision changed" in res.json()["detail"]
