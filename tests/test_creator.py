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


class MockKeitaroClient(KeitaroGateway):
    def __init__(self):
        self.campaigns = {}
        self.streams = {}
        self.offers = {
            5001: {"id": 5001, "name": "Test Offer Alpha"},
            5002: {"id": 5002, "name": "Test Offer Beta"},
        }
        self.domains = [{"id": 101, "name": "track.example.com", "domain": "track.example.com"}]
        self.groups = [{"id": 201, "name": "Test Group"}]
        self.sources = [{"id": 301, "name": "Google Ads"}]
        self.created_stream_calls = []

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
        return self.campaigns.get(campaign_id, {"id": campaign_id, "name": "Camp"})

    async def create_campaign(self, payload: dict[str, Any]) -> dict[str, Any]:
        camp_id = 9001
        res = {
            "id": camp_id,
            "name": payload["name"],
            "alias": payload["alias"],
            "domain_id": payload.get("domain_id"),
            "group_id": payload.get("group_id"),
        }
        self.campaigns[camp_id] = res
        return res

    async def get_campaign_streams(self, campaign_id: int) -> list[dict[str, Any]]:
        return [s for s in self.streams.values() if s.get("campaign_id") == campaign_id]

    async def create_stream(self, payload: dict[str, Any]) -> dict[str, Any]:
        s_id = 8000 + len(self.streams) + 1
        stream_obj = {**payload, "id": s_id}
        self.streams[s_id] = stream_obj
        self.created_stream_calls.append(stream_obj)
        return stream_obj

    async def get_stream(self, stream_id: int) -> dict[str, Any]:
        return self.streams.get(stream_id, {"id": stream_id})

    async def update_stream(self, stream_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        if stream_id in self.streams:
            self.streams[stream_id].update(payload)
        return self.streams.get(stream_id, {"id": stream_id})


@pytest.fixture
def mock_kt():
    return MockKeitaroClient()


@pytest.fixture(autouse=True)
def setup_test_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(mock_kt, monkeypatch):
    monkeypatch.setattr(settings, "KT_MODE", "demo")
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_keitaro_client] = lambda: mock_kt
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_creator_accepts_authenticated_real_mode(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "KT_MODE", "real")
    from src.core.security import generate_csrf_token, get_security_store, hash_token

    token = get_security_store().create_session(key="synthetic-creator-fixture")
    csrf = generate_csrf_token(hash_token(token))
    client.cookies.set("adrobot_session", token)
    payload = {
        "name": "summer_promo",
        "geo": "AU,RO",
        "offer_id": 5001,
        "domain_id": 101,
        "group_id": "201",
        "traffic_source_id": 301,
    }
    response = client.post("/api/campaigns", json=payload, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 201
    assert response.json()["name"] == "summer_promo"


def test_creator_success_creates_two_flows(client: TestClient, mock_kt: MockKeitaroClient):
    payload = {
        "name": "summer_promo",
        "geo": "AU,RO",
        "offer_id": 5001,
        "domain_id": 101,
        "group_id": "201",
        "traffic_source_id": 301,
    }

    response = client.post("/api/campaigns", json=payload)
    assert response.status_code == 201
    data = response.json()

    assert data["remote_campaign_id"] == 9001
    assert data["name"] == "summer_promo"
    assert data["verification_status"] == "verified"
    assert data["flow1_stream_id"] is not None
    assert data["flow2_stream_id"] is not None

    # Check streams created in Mock KT
    assert len(mock_kt.created_stream_calls) == 2

    # Flow 1: Country filter with AU, RO -> Google redirect
    flow1 = next(s for s in mock_kt.created_stream_calls if s["name"] == "Flow 1")
    assert flow1["type"] == "regular"
    assert flow1["schema"] == "redirect"
    assert flow1["action_payload"] == "https://google.com/"
    assert flow1["filters"][0]["payload"] == ["AU", "RO"]

    # Flow 2: Default fallback -> Offer 5001 with 100%
    flow2 = next(s for s in mock_kt.created_stream_calls if s["name"] == "Flow 2")
    assert flow2["type"] == "default"
    assert flow2["schema"] == "landings"
    assert flow2["offers"][0]["offer_id"] == 5001
    assert flow2["offers"][0]["share"] == 100


@pytest.mark.parametrize(
    ("offer_ids", "expected_offers"),
    [
        ([5001], [{"id": 5001, "name": "Test Offer Alpha", "share": 100}]),
        (
            [5002, 5001, 5003],
            [
                {"id": 5002, "name": "Test Offer Beta", "share": 34},
                {"id": 5001, "name": "Test Offer Alpha", "share": 33},
                {"id": 5003, "name": "Offer <Gamma> & Co", "share": 33},
            ],
        ),
    ],
)
def test_creation_result_contains_confirmed_routing(
    client: TestClient, mock_kt: MockKeitaroClient, offer_ids, expected_offers
):
    mock_kt.offers[5003] = {"id": 5003, "name": "Offer <Gamma> & Co"}
    payload = {
        "name": "routing_result",
        "geo": " au,RO,AU ",
        "offer_ids": offer_ids,
        "idempotency_key": "routing-result-check",
    }
    response = client.post("/api/campaigns", json=payload)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["flow1_countries"] == ["Австралия", "Румыния"]
    assert data["flow1_redirect_url"] == "https://google.com/"
    assert data["flow2_offers"] == expected_offers

    # A repeat returns the creation snapshot, without creating more streams or
    # replacing confirmed names with later catalog changes.
    mock_kt.offers[5001]["name"] = "Renamed after creation"
    repeated = client.post("/api/campaigns", json=payload)
    assert repeated.status_code == 201, repeated.text
    assert repeated.json() == data
    assert len(mock_kt.created_stream_calls) == 2


def test_creator_validation_invalid_geo(client: TestClient):
    payload = {
        "name": "summer_promo",
        "geo": "INVALID_GEO_123",
        "offer_id": 5001,
    }
    response = client.post("/api/campaigns", json=payload)
    assert response.status_code == 422
    assert "Invalid country code" in response.json()["detail"]


def test_creator_validation_missing_offer(client: TestClient):
    payload = {
        "name": "summer_promo",
        "geo": "AU",
        "offer_id": 99999,  # Offer does not exist
    }
    response = client.post("/api/campaigns", json=payload)
    assert response.status_code == 422
    assert "not found or unavailable" in response.json()["detail"]


def test_creator_multi_offers_success(client: TestClient, mock_kt: MockKeitaroClient):
    payload = {
        "name": "split_test_campaign",
        "geo": "AU",
        "offer_ids": [5001, 5002],
        "domain_id": 101,
        "group_id": "201",
    }

    response = client.post("/api/campaigns", json=payload)
    assert response.status_code == 201
    data = response.json()

    assert data["verification_status"] == "verified"
    assert "Flow 2" in data["routing_summary"]

    flow2 = next(s for s in mock_kt.created_stream_calls if s["name"] == "Flow 2")
    assert len(flow2["offers"]) == 2
    assert flow2["offers"][0]["offer_id"] == 5001
    assert flow2["offers"][0]["share"] == 50
    assert flow2["offers"][1]["offer_id"] == 5002
    assert flow2["offers"][1]["share"] == 50
    assert sum(o["share"] for o in flow2["offers"]) == 100


def test_creator_traffic_source_dict_parameters(client: TestClient, mock_kt: MockKeitaroClient):
    mock_kt.sources = [
        {
            "id": 302,
            "name": "Google Ads Advanced",
            "parameters": {
                "source": {"name": "utm_source", "placeholder": "{network}"},
                "campaign": {"name": "utm_campaign", "placeholder": "{campaignid}"},
            },
        }
    ]

    payload = {
        "name": "dict_params_camp",
        "geo": "AU",
        "offer_id": 5001,
        "domain_id": 101,
        "traffic_source_id": 302,
    }

    response = client.post("/api/campaigns", json=payload)
    assert response.status_code == 201
    data = response.json()

    assert "utm_source={network}" in data["tracking_url"]
    assert "utm_campaign={campaignid}" in data["tracking_url"]
