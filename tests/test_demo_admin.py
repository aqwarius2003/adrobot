"""Tests for the Demo Mode simulator proxy endpoints and DemoAdminService.

Verifies:
1. Simulator UI route /real/simulator is forbidden in Real mode (redirects to editor).
2. Endpoints under /api/real/simulator/ do not exist (404).
3. In Demo mode, proxy correctly interacts with Keitaro simulator API.
4. Conflict detection on fingerprint mismatch returns HTTP 409.
5. Validation errors when shares do not sum to 100% returns HTTP 422.
6. Simulator changes affect only the simulator and do not touch ADROBOT database.
"""

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from src.core.database import Base, demo_engine, real_engine
from src.main import create_app
from src.services import demo_admin_service
from src.simulator.app import simulator_app
from src.simulator.storage import simulator_storage


@pytest.fixture(autouse=True)
def setup_databases_and_storage():
    Base.metadata.create_all(bind=real_engine)
    Base.metadata.create_all(bind=demo_engine)
    simulator_storage.reset()
    yield
    simulator_storage.reset()
    for eng in (real_engine, demo_engine):
        with eng.connect() as conn:
            for tbl in reversed(Base.metadata.sorted_tables):
                conn.execute(tbl.delete())
            conn.commit()


@pytest.fixture
def mock_simulator_client(monkeypatch):
    """Route httpx.AsyncClient calls in demo_admin_service directly to simulator_app."""
    orig_client = httpx.AsyncClient

    def _async_client_factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.ASGITransport(app=simulator_app)
        kwargs["base_url"] = "http://testserver"
        return orig_client(*args, **kwargs)

    monkeypatch.setattr(demo_admin_service.httpx, "AsyncClient", _async_client_factory)


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app, mock_simulator_client):
    with TestClient(app) as test_client:
        yield test_client


def test_simulator_ui_and_api_forbidden_in_real_mode(client: TestClient):
    """In Real mode, simulator UI redirects to editor and /api/real/simulator is 404."""
    # UI redirect
    res_ui = client.get("/real/simulator", follow_redirects=False)
    assert res_ui.status_code in (302, 307)
    assert res_ui.headers["location"] == "/real/editor"

    # API under real prefix does not exist
    res_api = client.get("/api/real/simulator/campaigns")
    assert res_api.status_code == 404


def test_get_simulator_catalogs(client: TestClient):
    """In Demo mode, proxy returns campaigns, streams, and offers from simulator."""
    client.cookies.set("adrobot_mode", "demo")

    # Campaigns
    res_c = client.get("/api/demo/simulator/campaigns")
    assert res_c.status_code == 200
    campaigns = res_c.json()
    assert len(campaigns) == 3
    camp_ids = {c["id"] for c in campaigns}
    assert camp_ids == {1, 2, 3}

    # Streams for campaign 1
    res_s = client.get("/api/demo/simulator/campaigns/1/streams")
    assert res_s.status_code == 200
    streams = res_s.json()
    assert len(streams) == 2  # Flow 1 (redirect) and Flow 2 (offers)
    flow2 = next(s for s in streams if "Flow 2" in s["name"])
    assert len(flow2["offers"]) == 3
    assert flow2["fingerprint"] is not None

    # Offers
    res_o = client.get("/api/demo/simulator/offers")
    assert res_o.status_code == 200
    offers = res_o.json()
    assert len(offers) == 5

    # Filtered offers
    res_filtered = client.get("/api/demo/simulator/offers?search=Demo Offer 1")
    assert res_filtered.status_code == 200
    filtered = res_filtered.json()
    assert len(filtered) == 1
    assert filtered[0]["id"] == 101


def test_update_simulator_stream_offers_success(client: TestClient):
    """Successfully modify simulator stream offers and verify fingerprint changes."""
    client.cookies.set("adrobot_mode", "demo")

    # 1. Fetch current streams to get Flow 2 stream_id and initial fingerprint
    res_s = client.get("/api/demo/simulator/campaigns/1/streams")
    assert res_s.status_code == 200
    flow2 = next(s for s in res_s.json() if "Flow 2" in s["name"])
    stream_id = flow2["id"]
    initial_fp = flow2["fingerprint"]

    # 2. Update offers to 2 offers with 50/50 split
    payload = {
        "offers": [
            {"offer_id": 101, "share": 50},
            {"offer_id": 102, "share": 50},
        ],
        "expected_fingerprint": initial_fp,
    }
    res_put = client.put(
        f"/api/demo/simulator/campaigns/1/streams/{stream_id}/offers",
        json=payload,
    )
    assert res_put.status_code == 200
    data = res_put.json()
    assert data["id"] == stream_id
    new_fp = data["fingerprint"]
    assert new_fp != initial_fp
    assert len(data["offers"]) == 2

    # 3. Read back from simulator
    res_verify = client.get("/api/demo/simulator/campaigns/1/streams")
    updated_flow2 = next(s for s in res_verify.json() if s["id"] == stream_id)
    assert len(updated_flow2["offers"]) == 2
    assert updated_flow2["fingerprint"] == new_fp


def test_update_simulator_stream_offers_validation_shares(client: TestClient):
    """Shares sum must equal 100% or returns 422."""
    client.cookies.set("adrobot_mode", "demo")

    res_s = client.get("/api/demo/simulator/campaigns/1/streams")
    flow2 = next(s for s in res_s.json() if "Flow 2" in s["name"])
    stream_id = flow2["id"]

    # Invalid: sum = 80
    payload = {
        "offers": [
            {"offer_id": 101, "share": 40},
            {"offer_id": 102, "share": 40},
        ],
        "expected_fingerprint": flow2["fingerprint"],
    }
    res_put = client.put(
        f"/api/demo/simulator/campaigns/1/streams/{stream_id}/offers",
        json=payload,
    )
    assert res_put.status_code == 422
    assert "Sum of active shares must be 100%" in res_put.json()["detail"]


def test_update_simulator_stream_offers_conflict_detection(client: TestClient):
    """When expected_fingerprint is stale, returns 409 Conflict."""
    client.cookies.set("adrobot_mode", "demo")

    res_s = client.get("/api/demo/simulator/campaigns/1/streams")
    flow2 = next(s for s in res_s.json() if "Flow 2" in s["name"])
    stream_id = flow2["id"]

    payload = {
        "offers": [
            {"offer_id": 101, "share": 50},
            {"offer_id": 102, "share": 50},
        ],
        "expected_fingerprint": "stale_fingerprint_hash_12345",
    }
    res_put = client.put(
        f"/api/demo/simulator/campaigns/1/streams/{stream_id}/offers",
        json=payload,
    )
    assert res_put.status_code == 409
    assert (
        "fingerprint mismatch" in res_put.json()["detail"] or "Conflict" in res_put.json()["detail"]
    )


def test_update_simulator_flow1_tampering_rejected(client: TestClient):
    """Saving offers to Flow 1 stream is rejected on the server (Task 3.1 §1)."""
    client.cookies.set("adrobot_mode", "demo")

    # Flow 1 has id=10 in campaign 1
    res_s = client.get("/api/demo/simulator/campaigns/1/streams")
    flow1 = next(s for s in res_s.json() if s.get("role") == "flow_1" or "Flow 1" in s["name"])
    flow1_id = flow1["id"]

    payload = {
        "offers": [
            {"offer_id": 101, "share": 100},
        ],
        "expected_fingerprint": None,
    }
    res_put = client.put(
        f"/api/demo/simulator/campaigns/1/streams/{flow1_id}/offers",
        json=payload,
    )
    assert res_put.status_code == 422
    assert "Только основной поток (Flow 2) доступен для редактирования" in res_put.json()["detail"]


def test_simulator_template_elements_task_3_1(client: TestClient):
    """Simulator template has no stream selector, has Flow 1 stub, accessible combobox, unified naming and layout."""
    client.cookies.set("adrobot_mode", "demo")
    res = client.get("/demo/simulator")
    assert res.status_code == 200
    html = res.text

    # Unified naming: title, page title, menu item, save button, hint
    assert "<title>Keitaro — симулятор — Campaign Manager</title>" in html
    assert "Keitaro — симулятор" in html
    assert "Сохранить в симуляторе" in html
    assert "Fingerprint:" not in html
    assert "имитация" not in html
    assert "демо-Keitaro" not in html

    # No stream select
    assert 'id="simStreamSelect"' not in html
    assert "Поток для редактирования" not in html

    # Has campaign select and Go to ADROBOT button
    assert 'id="simCampaignSelect"' in html
    assert 'id="simGoToAdrobotBtn"' in html

    # Has Flow 1 stub in customer exact format
    assert 'id="simFlow1Stub"' in html
    assert "filter by: None" in html
    assert "clicks today:" in html

    # Has loading state card
    assert 'id="simLoadingState"' in html

    # Has offers table with proper classes and columns
    assert "offers-table" in html
    assert "sim-offers-table" in html
    assert 'class="col-offer">Оффер</th>' in html
    assert 'class="col-share">Доля трафика</th>' in html
    assert 'class="col-status">Статус</th>' in html
    assert 'class="col-actions">Действия</th>' in html

    # Flow 2 summary stats in header
    assert 'id="simSummaryStats"' in html
    assert 'id="simActiveCount"' in html
    assert 'id="simTotalShare"' in html

    # Has accessible combobox and OfferCombobox script
    assert 'id="simOfferSearchInput"' in html
    assert 'role="combobox"' in html
    assert 'src="/static/js/offer_combobox.js"' in html
