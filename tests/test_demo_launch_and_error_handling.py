from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from src.api import routes_demo
from src.core.config import settings
from src.core.database import DemoSessionLocal, init_db
from src.domain.models import Campaign, Stream, StreamOffer
from src.main import create_app
from src.services import demo_admin_service, keitaro_client
from src.services.keitaro_client import KeitaroApiError
from src.simulator.app import simulator_app


@pytest.fixture(autouse=True)
def route_simulator_transport(monkeypatch):
    """Route httpx.AsyncClient calls for simulator to ASGITransport in tests for deterministic speed."""
    orig_client = httpx.AsyncClient

    class SelectiveTransport(httpx.AsyncBaseTransport):
        def __init__(self):
            self.asgi = httpx.ASGITransport(app=simulator_app)
            self.real_http = httpx.AsyncHTTPTransport()

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.url.port == 59998:
                return await self.real_http.handle_async_request(request)
            return await self.asgi.handle_async_request(request)

    def _client_factory(*args, **kwargs):
        kwargs["transport"] = SelectiveTransport()
        return orig_client(*args, **kwargs)

    monkeypatch.setattr(keitaro_client.httpx, "AsyncClient", _client_factory)
    monkeypatch.setattr(demo_admin_service.httpx, "AsyncClient", _client_factory)
    monkeypatch.setattr(routes_demo.httpx, "AsyncClient", _client_factory)


@pytest.fixture
def app_client():
    init_db()
    app = create_app()
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        yield client


def test_1_clean_local_http_launch(app_client: TestClient):
    """1. Local HTTP launch: APP_ENV=development allows HTTP access and plain login."""
    original_env = settings.APP_ENV
    original_mode = settings.KT_MODE
    try:
        settings.APP_ENV = "development"
        settings.KT_MODE = "demo"

        # Health endpoint verifies development and demo mode
        res = app_client.get("/api/health")
        assert res.status_code == 200
        health = res.json()
        assert health["status"] == "ok"
        assert health["environment"] == "development"

        # Root redirect goes to /demo/editor
        res_root = app_client.get("/", follow_redirects=False)
        assert res_root.status_code == 307
        assert res_root.headers["location"] == "/demo/editor"

        # Real login over HTTP is permitted in development (localhost dev exception)
        with patch("src.services.auth_service.AuthService.authenticate") as mock_auth:
            mock_auth.return_value = {
                "success": True,
                "token": "valid-dev-token",
                "csrf_token": "dev-csrf",
            }
            login_res = app_client.post(
                "/api/auth/login",
                json={"api_key": "some-key"},
                headers={"Host": "127.0.0.1:8000"},
            )
            assert login_res.status_code == 200
            assert "adrobot_session" in login_res.cookies
            # In development over HTTP, cookie must not require Secure
            session_cookie = login_res.headers.get("set-cookie", "")
            assert "Secure" not in session_cookie
    finally:
        settings.APP_ENV = original_env
        settings.KT_MODE = original_mode


def test_2_open_demo_without_key(app_client: TestClient):
    """2. Opening demo without key: accessible without credentials or session cookies."""
    # GET /demo/editor loads HTML without credentials
    res_page = app_client.get("/demo/editor")
    assert res_page.status_code == 200
    assert "Campaign Manager" in res_page.text

    # GET /api/demo/campaigns returns 200 with demo campaigns list
    res_api = app_client.get("/api/demo/campaigns")
    assert res_api.status_code == 200
    campaigns = res_api.json()
    assert isinstance(campaigns, list)
    assert len(campaigns) > 0


def test_3_and_4_fetch_remove_push_and_verify_simulator(app_client: TestClient):
    """3. Fetch -> Remove -> Push to Demo, and
    4. Check published composition and percentages on simulator page."""
    # 3.1 Fetch streams into local demo database
    res_fetch = app_client.post("/api/demo/campaigns/1/fetch-streams")
    assert res_fetch.status_code == 200
    camp_data = res_fetch.json()
    assert camp_data["remote_campaign_id"] == 1
    assert len(camp_data["streams"]) >= 2

    # Identify managed stream (Flow 2)
    managed_stream = next((s for s in camp_data["streams"] if s["is_managed"]), None)
    assert managed_stream is not None, "Managed stream must exist"
    stream_id = managed_stream["id"]
    active_offers = [o for o in managed_stream["offers"] if o["state"] == "active"]
    assert len(active_offers) >= 2, "Need at least 2 active offers for Remove test"

    target_offer_id = active_offers[0]["offer_id"]

    # 3.2 Remove first offer (moves to archive, recalculates weights to 100%)
    res_remove = app_client.post(
        f"/api/demo/streams/{stream_id}/offers/{target_offer_id}/remove",
        json={
            "expected_revision": managed_stream["revision"],
            "expected_remote_campaign_id": 1,
        },
    )
    assert res_remove.status_code == 200
    stream_after_remove = res_remove.json()
    assert stream_after_remove["sync_status"] == "dirty"

    remaining_active = [o for o in stream_after_remove["offers"] if o["state"] == "active"]
    assert sum(o["share"] for o in remaining_active) == 100

    # 3.3 Push to Demo simulator
    res_push = app_client.post(
        f"/api/demo/streams/{stream_id}/push",
        json={
            "expected_revision": stream_after_remove["revision"],
            "expected_remote_campaign_id": 1,
        },
    )
    assert res_push.status_code == 200
    stream_after_push = res_push.json()
    assert stream_after_push["sync_status"] == "clean"

    # 4. Verify simulator received the update and published shares sum to 100%
    res_sim_streams = app_client.get("/api/demo/simulator/campaigns/1/streams")
    assert res_sim_streams.status_code == 200
    sim_streams = res_sim_streams.json()
    sim_flow2 = next(
        (s for s in sim_streams if s["id"] == managed_stream["remote_stream_id"]), None
    )
    assert sim_flow2 is not None

    sim_offers = sim_flow2.get("offers", [])
    sim_active = [o for o in sim_offers if o.get("state", "active") == "active"]
    # Target offer is no longer in active simulator offers
    assert all(o["offer_id"] != target_offer_id for o in sim_active)
    # Simulator shares sum strictly to 100%
    assert sum(o["share"] for o in sim_active) == 100


def test_5_unreachable_simulator_clear_error(app_client: TestClient):
    """5. Clear error reporting when simulator is unreachable."""
    original_url = settings.DEMO_SIMULATOR_URL
    try:
        # Point to a closed/invalid port
        settings.DEMO_SIMULATOR_URL = "http://127.0.0.1:59998"

        res = app_client.get("/api/demo/simulator/campaigns")
        assert res.status_code == 502
        data = res.json()
        assert "detail" in data
        assert "connect" in data["detail"].lower() or "59998" in data["detail"]
    finally:
        settings.DEMO_SIMULATOR_URL = original_url


def test_5_push_connection_loss_yields_unknown_status(app_client: TestClient):
    """5b. Connection loss during Push PUT preserves draft and leaves sync_status='unknown'."""
    from src.domain.weights import compute_stream_fingerprint

    remote_state = {
        "id": 9991,
        "offers": [{"offer_id": 1, "share": 50, "state": "active"}],
    }
    matching_fp = compute_stream_fingerprint(remote_state)

    # Setup test stream in local db
    db = DemoSessionLocal()
    try:
        camp = Campaign(
            name="test_rykov_Test Camp Unknown",
            alias="test_alias_unknown",
            remote_campaign_id=999,
        )
        db.add(camp)
        db.commit()

        st = Stream(
            campaign_id=camp.id,
            remote_stream_id=9991,
            name="Flow 2",
            is_managed=True,
            sync_status="dirty",
            revision=1,
            remote_fingerprint=matching_fp,
        )
        db.add(st)
        db.commit()

        so = StreamOffer(
            stream_id=st.id,
            offer_id=1,  # synthetic
            draft_share=100,
            draft_state="active",
            baseline_share=50,
            baseline_state="active",
        )
        db.add(so)
        db.commit()
        st_id = st.id
    finally:
        db.close()

    # Preflight get_stream succeeds; update_stream fails with network error; subsequent reconcile get_stream also fails
    with patch("src.services.keitaro_client.KeitaroClient.get_stream") as mock_get:
        mock_get.side_effect = [
            remote_state,  # 1. Preflight check succeeds
            KeitaroApiError("Connection still down"),  # 2. Reconcile read-back also fails
        ]
        with patch("src.services.keitaro_client.KeitaroClient.update_stream") as mock_put:
            mock_put.side_effect = KeitaroApiError(
                "Network error connecting to Keitaro: Connection reset"
            )

            res = app_client.post(
                f"/api/demo/streams/{st_id}/push",
                json={"expected_revision": 1, "expected_remote_campaign_id": 999},
            )
            assert res.status_code == 200
            data = res.json()
            assert data["sync_status"] == "unknown"

            # Check DB status
            db_verify = DemoSessionLocal()
            try:
                db_st = db_verify.query(Stream).filter_by(id=st_id).first()
                assert db_st.sync_status == "unknown", (
                    "Must mark sync_status as unknown on connection loss"
                )
                # Draft offers must be preserved
                assert db_st.stream_offers[0].draft_share == 100
                assert db_st.stream_offers[0].draft_state == "active"
            finally:
                db_verify.close()


def test_6_no_csrf_needed_for_demo(app_client: TestClient):
    """6. Mutating requests in demo mode execute without requiring CSRF token."""
    db = DemoSessionLocal()
    try:
        camp = db.query(Campaign).first()
        st = db.query(Stream).filter_by(is_managed=True).first()
        camp_id = camp.remote_campaign_id if camp else 1
        st_id = st.id if st else 1
    finally:
        db.close()

    res = app_client.post(
        f"/api/demo/streams/{st_id}/cancel",
        json={"expected_remote_campaign_id": camp_id},
        # Deliberately omit X-CSRF-Token
    )
    # Must succeed (200) without CSRF rejection
    assert res.status_code == 200


def test_7_production_strictly_requires_https_for_real_mode(app_client: TestClient):
    """7. In production, real mode login strictly requires HTTPS."""
    original_env = settings.APP_ENV
    try:
        settings.APP_ENV = "production"

        # Plain HTTP request in production
        res = app_client.post(
            "/api/auth/login",
            json={"api_key": "any-key"},
            headers={"Host": "127.0.0.1:8000"},  # HTTP scheme
        )
        assert res.status_code == 403
        data = res.json()
        assert "HTTPS is strictly required for real mode in production" in data["detail"]
    finally:
        settings.APP_ENV = original_env
