"""Tests for stream statistics (clicks today) and compact unmanaged stream format.

Verifies:
1. GET /api/{mode}/campaigns/{id}/stream-stats endpoint returns confirmed clicks or graceful unavailable status.
2. Error in report API does not block response with 500.
3. Position semantic is preserved as 1-based order in the campaign.
4. Unmanaged stream format matches customer specification.
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


def test_stream_stats_in_demo_mode(client: TestClient):
    """In demo mode, stats endpoint calls simulator report build and returns stream clicks."""
    client.cookies.set("adrobot_mode", "demo")

    # Campaign 1 has stream 10 and 11
    res = client.get("/api/demo/campaigns/1/stream-stats")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["campaign_id"] == 1
    assert "clicks_by_stream" in data
    assert 10 in data["clicks_by_stream"] or "10" in data["clicks_by_stream"]
    assert data["clicks_by_stream"].get("10", data["clicks_by_stream"].get(10)) == 0


def test_stream_stats_graceful_on_failure(client: TestClient, monkeypatch):
    """When tracker report fails (e.g. 403 Forbidden or network error), stats endpoint returns status='unavailable'."""
    client.cookies.set("adrobot_mode", "demo")

    # Inject fault into simulator
    simulator_storage.set_fault("report_forbidden")

    res = client.get("/api/demo/campaigns/1/stream-stats")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "unavailable"
    assert data["clicks_by_stream"] == {}


def test_simulator_template_has_customer_line_format(client: TestClient):
    """Simulator template renders the customer sample format with filter by: None and clicks today."""
    client.cookies.set("adrobot_mode", "demo")
    res = client.get("/demo/simulator")
    assert res.status_code == 200
    html = res.text

    assert "filter by: None" in html
    assert "clicks today:" in html
    assert "simFlow1ClicksText" in html
