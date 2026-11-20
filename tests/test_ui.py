from fastapi.testclient import TestClient

from src.core.config import settings
from src.main import create_app


def test_ui_creator_page_demo_mode(monkeypatch):
    monkeypatch.setattr(settings, "KT_MODE", "demo")
    app = create_app()
    client = TestClient(app)
    response = client.get("/creator")
    assert response.status_code == 200
    assert "Создание кампании" in response.text
    assert "Новая кампания" in response.text
    assert "По странам → Google" in response.text
    assert "Остальные посетители → офферы" in response.text
    # One editable routing view, without duplicate offer controls. DOM order is
    # also the native keyboard order: common fields, GEO, then offers and submit.
    assert "routing-preview-box" not in response.text
    assert 'id="selectedOffersContainer"' not in response.text
    field_order = [
        "campaignName",
        "domainSelect",
        "groupSelect",
        "sourceSelect",
        "geoSearchInput",
        "offerSearchInput",
        "submitCreateBtn",
    ]
    positions = [response.text.index(f'id="{field}"') for field in field_order]
    assert positions == sorted(positions)


def test_ui_creator_page_real_mode_redirects(monkeypatch):
    monkeypatch.setattr(settings, "KT_MODE", "real")
    app = create_app()
    client = TestClient(app)
    response = client.get("/creator", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/real/creator"


def test_ui_editor_page(monkeypatch):
    monkeypatch.setattr(settings, "KT_MODE", "demo")
    app = create_app()
    client = TestClient(app)
    response = client.get("/demo/editor")
    assert response.status_code == 200
    assert "Редактор кампаний" in response.text
    assert "Fetch Streams" in response.text
    assert "streamsContainer" in response.text


def test_ui_editor_view_in_kt_button_mode_visibility(monkeypatch):
    # Real mode: viewInKtLink is rendered with proper target and rel
    monkeypatch.setattr(settings, "KT_MODE", "real")
    app_real = create_app()
    client_real = TestClient(app_real)
    from src.core.security import get_security_store

    token = get_security_store().create_session(key="synthetic-ui-fixture")
    client_real.cookies.set("adrobot_session", token)
    resp_real = client_real.get("/real/editor")
    assert resp_real.status_code == 200
    assert 'id="viewInKtLink"' in resp_real.text
    assert 'target="_blank"' in resp_real.text
    assert 'rel="noopener noreferrer"' in resp_real.text
    assert "View in KT" in resp_real.text

    # Demo mode: viewInKtLink is excluded from DOM
    monkeypatch.setattr(settings, "KT_MODE", "demo")
    app_demo = create_app()
    client_demo = TestClient(app_demo)
    resp_demo = client_demo.get("/demo/editor")
    assert resp_demo.status_code == 200
    assert 'id="viewInKtLink"' not in resp_demo.text


def test_root_redirect_depends_on_mode(monkeypatch):
    # In demo mode, root redirects to editor
    monkeypatch.setattr(settings, "KT_MODE", "demo")
    app = create_app()
    client = TestClient(app)
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] in ("/demo/editor", "/editor")

    # In real mode with session, root redirects to editor
    monkeypatch.setattr(settings, "KT_MODE", "real")
    app_real = create_app()
    client_real = TestClient(app_real)
    from src.core.security import get_security_store

    token = get_security_store().create_session(key="synthetic-ui-fixture")
    client_real.cookies.set("adrobot_session", token)
    response_real = client_real.get("/", follow_redirects=False)
    assert response_real.status_code in (302, 307)
    assert response_real.headers["location"] in ("/real/editor", "/editor")
