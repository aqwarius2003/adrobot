import pytest
from fastapi.testclient import TestClient

from src.core.config import settings
from src.core.database import Base, demo_engine, real_engine
from src.main import create_app


@pytest.fixture(autouse=True)
def setup_databases():
    Base.metadata.create_all(bind=real_engine)
    Base.metadata.create_all(bind=demo_engine)
    yield
    for eng in (real_engine, demo_engine):
        with eng.connect() as conn:
            for tbl in reversed(Base.metadata.sorted_tables):
                conn.execute(tbl.delete())
            conn.commit()


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_csrf(client: TestClient, monkeypatch):
    from unittest.mock import patch

    import httpx

    monkeypatch.setattr(settings, "REAL_KEITARO_API_KEY", "test-key-123")
    monkeypatch.setattr(settings, "REAL_KEITARO_BASE_URL", "https://tracker.test.com")
    mock_resp = httpx.Response(200, json=[{"id": 1}])
    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        res = client.post("/api/auth/login", json={"api_key": "test-key-123"})
        csrf = res.json().get("csrf_token")
    return csrf


def test_initial_visit_redirects_to_login(client: TestClient, monkeypatch):
    """First visit to root / without session cookie in real mode redirects to /login."""
    monkeypatch.setattr(settings, "KT_MODE", "real")
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 307
    assert res.headers["location"] == "/login"


def test_root_visit_with_demo_cookie_redirects_to_demo_editor(client: TestClient):
    """Visit to root with demo session cookie opens demo editor."""
    client.cookies.set("adrobot_mode", "demo")
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 307
    assert res.headers["location"] == "/demo/editor"


def test_legacy_editor_url_preserves_query_params(client: TestClient, auth_csrf):
    """Authenticated /editor?campaign_id=102 redirects to explicit /{mode}/editor?campaign_id=102."""
    res = client.get("/editor?campaign_id=102&test=1", follow_redirects=False)
    assert res.status_code == 307
    assert res.headers["location"] == "/real/editor?campaign_id=102&test=1"


def test_real_editor_page_elements(client: TestClient, auth_csrf):
    """Real editor must display 'Подключение: Keitaro', no simulator tab, and real app config."""
    res = client.get("/real/editor")
    assert res.status_code == 200
    html = res.text
    assert "Подключение: Keitaro" in html
    assert "Keitaro — симулятор" not in html
    assert '"is_demo": false' in html
    assert '"current_mode": "real"' in html
    assert "mode-btn" in html


def test_demo_editor_page_elements(client: TestClient):
    """Demo editor must display 'Демо · симулятор', simulator tab link, and demo app config."""
    res = client.get("/demo/editor")
    assert res.status_code == 200
    html = res.text
    assert "Демо · симулятор" in html
    assert "Keitaro — симулятор" in html
    assert '"is_demo": true' in html
    assert '"current_mode": "demo"' in html


def test_real_simulator_page_forbidden(client: TestClient, auth_csrf):
    """Simulator tab is forbidden in real mode and redirects to /real/editor."""
    res = client.get("/real/simulator", follow_redirects=False)
    assert res.status_code == 307
    assert res.headers["location"] == "/real/editor"


def test_creator_in_real_mode_is_available_after_login(client: TestClient, auth_csrf):
    """Creator is restored; real writes still require CSRF."""
    res_get = client.get("/creator", follow_redirects=False)
    assert res_get.status_code == 307
    assert res_get.headers["location"] == "/real/creator"
    assert client.get("/real/creator").status_code == 200

    res_post = client.post(
        "/api/real/campaigns",
        json={"name": "test_camp", "geo": "KZ", "offer_ids": [101]},
    )
    assert res_post.status_code == 403
    assert "CSRF" in res_post.json()["detail"]


def test_session_context_endpoints(client: TestClient, auth_csrf):
    """GET and POST /api/session/context manage mode preference safely."""
    # 1. Context with active session is real
    res_get = client.get("/api/session/context")
    assert res_get.status_code == 200
    data = res_get.json()
    assert data["mode"] == "real"
    assert data["is_demo"] is False
    assert data["authenticated"] is True

    # 2. Rejection of invalid mode
    res_invalid = client.post("/api/session/context", json={"mode": "hack_mode"})
    assert res_invalid.status_code == 422

    # 3. Valid switch to demo sets cookie
    res_post = client.post(
        "/api/session/context",
        json={"mode": "demo"},
        headers={"X-CSRF-Token": auth_csrf},
    )
    assert res_post.status_code == 200
    post_data = res_post.json()
    assert post_data["mode"] == "demo"
    assert post_data["is_demo"] is True
    assert "adrobot_mode" in res_post.cookies
    assert res_post.cookies["adrobot_mode"] == "demo"

    # 4. Subsequent GET returns demo context
    res_get_demo = client.get("/api/session/context")
    assert res_get_demo.json()["mode"] == "demo"
    assert res_get_demo.json()["is_demo"] is True
    assert res_get_demo.json()["mode"] == "demo"
    assert res_get_demo.json()["is_demo"] is True
