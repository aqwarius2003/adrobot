"""Focused checks for restoring Part 1 without crossing connection boundaries."""

import json

import httpx
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from src.api.dependencies import get_creator_service, get_keitaro_client
from src.core.config import settings
from src.core.connection_context import ConnectionContext, get_connection_context
from src.core.database import init_db
from src.core.security import generate_csrf_token
from src.domain.schemas import CampaignCreateResponse
from src.main import app
from src.services.keitaro_client import KeitaroClient
from src.simulator.app import simulator_app
from src.simulator.storage import simulator_storage


@pytest.fixture
def creator_client(monkeypatch):
    # conftest binds all engines and simulator storage to temporary files.
    monkeypatch.setattr(settings, "REAL_KEITARO_BASE_URL", "https://real.invalid")
    monkeypatch.setattr(settings, "REAL_KEITARO_API_KEY", "synthetic-real-key")
    monkeypatch.setattr(settings, "DEMO_KEITARO_BASE_URL", "http://demo.invalid")
    monkeypatch.setattr(settings, "DEMO_KEITARO_API_KEY", "demo-api-key")
    init_db()

    class SimulatorGateway(KeitaroClient):
        async def _request(self, method, path, params=None, json_data=None):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=simulator_app), base_url=self.base_url
            ) as upstream:
                response = await upstream.request(
                    method,
                    f"/admin_api/v1/{path.lstrip('/')}",
                    params=params,
                    json=json_data,
                    headers=self._get_headers(),
                )
                response.raise_for_status()
                return response.json()

    def demo_gateway(context: ConnectionContext = Depends(get_connection_context)):
        assert context.is_demo, "Test must never contact a real tracker"
        return SimulatorGateway(base_url=context.base_url, api_key=context.api_key)

    app.dependency_overrides[get_keitaro_client] = demo_gateway
    client = TestClient(app, follow_redirects=False)
    yield client
    client.close()
    app.dependency_overrides.clear()


def test_demo_creator_is_visible_and_creates_simulator_campaign(creator_client):
    page = creator_client.get("/demo/creator")
    assert page.status_code == 200
    assert 'href="/demo/creator"' in page.text
    assert 'id="geoSearchInput"' in page.text
    assert '"api_prefix": "/api/demo"' in page.text

    created = creator_client.post(
        "/api/demo/campaigns",
        json={
            "name": "creator_restore_check",
            "geo": "KZ,CA",
            "offer_ids": [101, 102, 103],
        },
    )
    assert created.status_code == 201, created.text
    data = created.json()
    assert data["verification_status"] == "verified"
    remote_id = data["remote_campaign_id"]
    campaigns = creator_client.get("/api/demo/campaigns").json()
    assert any(c["remote_campaign_id"] == remote_id for c in campaigns)
    detail = creator_client.get(f"/api/demo/campaigns/{remote_id}").json()
    assert len(detail["streams"]) == 2
    managed = next(s for s in detail["streams"] if s["is_managed"])
    assert sorted(o["share"] for o in managed["offers"]) == [33, 33, 34]


def test_real_creator_requires_login(creator_client):
    page = creator_client.get("/real/creator")
    assert page.status_code == 307
    assert page.headers["location"] == "/login?return_to=/real/creator"
    denied = creator_client.post(
        "/api/real/campaigns",
        json={
            "name": "unauthorized",
            "geo": "CA",
            "offer_id": 101,
        },
    )
    assert denied.status_code == 401


def test_real_creator_keeps_csrf_and_accepts_authenticated_creation(creator_client, monkeypatch):
    session = {"token_hash": "synthetic-session-hash"}
    monkeypatch.setattr("src.web.routes_ui.get_current_session", lambda *a, **k: session)
    monkeypatch.setattr("src.core.security.get_current_session", lambda *a, **k: session)
    calls = []

    class CreatorStub:
        async def create_campaign(self, request):
            calls.append(request)
            return CampaignCreateResponse(
                id=1,
                remote_campaign_id=12345,
                name="test_restore",
                alias="restore",
                flow1_stream_id=1,
                flow2_stream_id=2,
                routing_summary="synthetic",
                verification_status="verified",
            )

    app.dependency_overrides[get_creator_service] = lambda: CreatorStub()
    page = creator_client.get("/real/creator")
    assert page.status_code == 200
    assert 'href="/real/creator"' in page.text
    config = json.loads(page.text.split('id="appConfigData">', 1)[1].split("</script>", 1)[0])
    assert config["api_prefix"] == "/api/real"
    assert config["csrf_token"]
    payload = {"name": "authenticated", "geo": "CA", "offer_id": 18813}
    assert creator_client.post("/api/real/campaigns", json=payload).status_code == 403
    assert not calls
    created = creator_client.post(
        "/api/real/campaigns",
        json=payload,
        headers={"X-CSRF-Token": generate_csrf_token(session["token_hash"])},
    )
    assert created.status_code == 201, created.text
    assert len(calls) == 1


def test_creator_keeps_entered_name_and_allows_exact_retry(creator_client):
    payload = {
        "name": "  Без префикса  ",
        "geo": "CA",
        "offer_id": 101,
        "idempotency_key": "creator-unprefixed-name-check",
    }
    created = creator_client.post("/api/demo/campaigns", json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["name"] == "Без префикса"
    assert created.json()["alias"].startswith("campaign-")
    retried = creator_client.post("/api/demo/campaigns", json=payload)
    assert retried.status_code == 201, retried.text
    assert retried.json()["remote_campaign_id"] == created.json()["remote_campaign_id"]
    assert retried.json()["name"] == "Без префикса"


def test_creator_rejects_existing_remote_name_before_writing(creator_client):
    # A campaign created outside ADROBOT must also reserve its name.
    simulator_storage.create_campaign({"name": "Проверка дубликата", "alias": "existing-name"})
    before = len(simulator_storage.get_campaigns())
    before_streams = len(simulator_storage.streams)
    response = creator_client.post(
        "/api/demo/campaigns",
        json={
            "name": "  ПРОВЕРКА ДУБЛИКАТА  ",
            "geo": "CA",
            "offer_id": 101,
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "campaign_name_exists"
    assert "другое название" in response.json()["detail"]
    assert len(simulator_storage.get_campaigns()) == before
    assert len(simulator_storage.streams) == before_streams


def test_creator_does_not_write_when_name_check_is_unavailable(creator_client, monkeypatch):
    async def unavailable(self):
        raise RuntimeError("synthetic catalog unavailable")

    monkeypatch.setattr(KeitaroClient, "get_campaigns", unavailable)
    before = len(simulator_storage.get_campaigns())
    response = creator_client.post(
        "/api/demo/campaigns",
        json={
            "name": "Не создавать без проверки имени",
            "geo": "CA",
            "offer_id": 101,
        },
    )
    assert response.status_code == 502, response.text
    assert len(simulator_storage.get_campaigns()) == before


def test_simulator_itself_rejects_duplicate_name(creator_client):
    with TestClient(simulator_app) as upstream:
        headers = {"Api-Key": "demo-api-key"}
        payload = {"name": "Уникальность симулятора", "alias": "sim-name-first"}
        assert (
            upstream.post("/admin_api/v1/campaigns", json=payload, headers=headers).status_code
            == 201
        )
        payload.update(name=" уникальность СИМУЛЯТОРА ", alias="sim-name-second")
        before = len(simulator_storage.get_campaigns())
        duplicate = upstream.post("/admin_api/v1/campaigns", json=payload, headers=headers)
        assert duplicate.status_code == 409
        assert len(simulator_storage.get_campaigns()) == before
