import os
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.dependencies import get_db, get_keitaro_client
from src.core.config import settings
from src.core.database import Base
from src.main import create_app
from src.services.keitaro_client import KeitaroGateway
from src.simulator.storage import SimulatorStorage

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


class MockBrowserKeitaro(KeitaroGateway):
    def __init__(self):
        self.campaigns = {
            1: {"id": 1, "name": "test_rykov_demo_flow", "alias": "demo-flow"},
        }
        self.streams = {
            10: {
                "id": 10,
                "campaign_id": 1,
                "name": "Flow 1 (GEO)",
                "type": "regular",
                "schema": "redirect",
                "offers": [],
            },
            11: {
                "id": 11,
                "campaign_id": 1,
                "name": "Flow 2 (Offers)",
                "type": "default",
                "schema": "landings_offers",
                "offers": [
                    {"offer_id": 101, "share": 50, "state": "active"},
                    {"offer_id": 102, "share": 50, "state": "active"},
                ],
            },
        }

    async def get_campaigns(self) -> list[dict[str, Any]]:
        return list(self.campaigns.values())

    async def get_campaign(self, campaign_id: int) -> dict[str, Any]:
        return self.campaigns.get(campaign_id, {"id": campaign_id})

    async def get_campaign_streams(self, campaign_id: int) -> list[dict[str, Any]]:
        return [s for s in self.streams.values() if s.get("campaign_id") == campaign_id]

    async def get_stream(self, stream_id: int) -> dict[str, Any]:
        return self.streams.get(stream_id, {"id": stream_id})

    async def update_stream(self, stream_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        if stream_id in self.streams:
            self.streams[stream_id].update(payload)
        return self.streams.get(stream_id, {"id": stream_id})

    async def get_offers(self) -> list[dict[str, Any]]:
        return [
            {"id": 101, "name": "Offer 101"},
            {"id": 102, "name": "Offer 102"},
            {"id": 103, "name": "Offer 103"},
        ]

    async def get_offer(self, offer_id: int) -> dict[str, Any]:
        return {"id": offer_id, "name": f"Offer {offer_id}"}


@pytest.fixture(autouse=True)
def init_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def mock_kt():
    return MockBrowserKeitaro()


@pytest.fixture
def client(mock_kt, monkeypatch):
    monkeypatch.setattr(settings, "KT_MODE", "demo")
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_keitaro_client] = lambda: mock_kt
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_browser_1_reopening_saved_draft(client: TestClient):
    """BROWSER-1: Reopening a campaign with unpublished changes via GET /api/campaigns/{id}

    must return the saved draft and dirty status without throwing conflict or wiping streams.
    """
    # 1. First fetch streams from Keitaro/simulator
    res_fetch = client.post("/api/campaigns/1/fetch-streams")
    assert res_fetch.status_code == 200
    data = res_fetch.json()
    assert len(data["streams"]) == 2
    flow2 = next(s for s in data["streams"] if s["is_managed"])
    assert flow2["sync_status"] == "clean"

    # 2. Modify an offer weight (local mutation without Push) -> becomes dirty
    res_mod = client.patch(
        f"/api/streams/{flow2['id']}/offers/101",
        json={"share": 80, "is_pinned": True, "expected_revision": flow2["revision"]},
    )
    assert res_mod.status_code == 200
    mod_stream = res_mod.json()
    assert mod_stream["sync_status"] == "dirty"
    off101 = next(o for o in mod_stream["offers"] if o["offer_id"] == 101)
    off102 = next(o for o in mod_stream["offers"] if o["offer_id"] == 102)
    assert off101["share"] == 80
    assert off101["is_pinned"] is True
    assert off102["share"] == 20

    # 3. Simulate page reload / new tab: GET /api/campaigns/1 directly
    res_detail = client.get("/api/campaigns/1")
    assert res_detail.status_code == 200
    detail = res_detail.json()
    flow2_reloaded = next(s for s in detail["streams"] if s["is_managed"])
    assert flow2_reloaded["sync_status"] == "dirty"
    r_101 = next(o for o in flow2_reloaded["offers"] if o["offer_id"] == 101)
    r_102 = next(o for o in flow2_reloaded["offers"] if o["offer_id"] == 102)
    assert r_101["share"] == 80
    assert r_101["is_pinned"] is True
    assert r_102["share"] == 20

    # 4. Explicit Fetch while dirty MUST be rejected with 409 Conflict
    res_dirty_fetch = client.post("/api/campaigns/1/fetch-streams")
    assert res_dirty_fetch.status_code == 409
    assert "unpublished local changes" in res_dirty_fetch.json()["detail"]

    # 5. Cancel restores clean Baseline 50/50
    res_cancel = client.post(f"/api/streams/{flow2['id']}/cancel")
    assert res_cancel.status_code == 200
    cancel_stream = res_cancel.json()
    assert cancel_stream["sync_status"] == "clean"
    c_101 = next(o for o in cancel_stream["offers"] if o["offer_id"] == 101)
    c_102 = next(o for o in cancel_stream["offers"] if o["offer_id"] == 102)
    assert c_101["share"] == 50
    assert c_101["is_pinned"] is False
    assert c_102["share"] == 50


def test_browser_2_fetch_context_synchronization(client: TestClient):
    """BROWSER-2: Direct access / lookup by remote_campaign_id allows immediate clean Fetch."""
    # 1. Fetch clean campaign
    res = client.post("/api/campaigns/1/fetch-streams")
    assert res.status_code == 200
    data = res.json()
    assert data["remote_campaign_id"] == 1

    # 2. Detail by remote_campaign_id works immediately
    res_get = client.get("/api/campaigns/1")
    assert res_get.status_code == 200
    assert res_get.json()["remote_campaign_id"] == 1

    # 3. Clean fetch can be repeated without errors
    res_repeat = client.post("/api/campaigns/1/fetch-streams")
    assert res_repeat.status_code == 200


def test_real_mode_creator_restored(client: TestClient, monkeypatch):
    """Part 1 is visible after login; restoring it does not remove CSRF."""
    monkeypatch.setattr(settings, "KT_MODE", "real")
    from src.core.security import get_security_store

    token = get_security_store().create_session()
    client.cookies.set("adrobot_session", token)

    # POST without CSRF remains rejected before any upstream request.
    res = client.post(
        "/api/campaigns",
        json={"name": "test_promo", "geo": "CA", "offer_ids": [101]},
    )
    assert res.status_code == 403
    assert "CSRF" in res.json()["detail"]

    # GET /creator redirects to the restored real creator.
    res_page = client.get("/creator", follow_redirects=False)
    assert res_page.status_code in (302, 307)
    assert res_page.headers["location"] == "/real/creator"
    assert client.get("/real/creator").status_code == 200

    # GET / redirects to editor in real mode
    res_root = client.get("/", follow_redirects=False)
    assert res_root.status_code in (302, 307)
    assert res_root.headers["location"] in ("/real/editor", "/editor")


def test_simulator_persistence_across_restarts(tmp_path):
    """Section 6: Simulator state survives restarts via persistence file."""
    state_file = str(tmp_path / "sim_state.json")

    # Instance 1: Create storage and mutate (3 synthetic seed campaigns per DEMO_MODE_TASK v3.0 §2)
    sim1 = SimulatorStorage(surname="rykov", state_file=state_file)
    initial_count = len(sim1.campaigns)
    assert initial_count == 3
    new_camp = sim1.create_campaign({"name": "test_rykov_persisted", "alias": "persisted"})
    camp_id = new_camp["id"]
    assert os.path.exists(state_file)

    # Instance 2: Start new simulator instance on the same file (restart simulation)
    sim2 = SimulatorStorage(surname="rykov", state_file=state_file)
    assert camp_id in sim2.campaigns
    assert sim2.campaigns[camp_id]["name"] == "test_rykov_persisted"

    # Reset restores seed state
    sim2.reset()
    assert camp_id not in sim2.campaigns
    assert len(sim2.campaigns) == initial_count
