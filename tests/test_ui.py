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
    assert f"test_{settings.CAMPAIGN_SURNAME.lower()}_" in response.text
    assert "routing-preview-box" in response.text


def test_ui_creator_page_real_mode_redirects(monkeypatch):
    monkeypatch.setattr(settings, "KT_MODE", "real")
    app = create_app()
    client = TestClient(app)
    response = client.get("/creator", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/editor?notice=creator_unavailable_in_real_mode"


def test_ui_editor_page():
    app = create_app()
    client = TestClient(app)
    response = client.get("/editor")
    assert response.status_code == 200
    assert "Редактор кампаний" in response.text
    assert "Fetch Streams" in response.text
    assert "streamsContainer" in response.text


def test_root_redirect_depends_on_mode(monkeypatch):
    # In demo mode, root redirects to creator
    monkeypatch.setattr(settings, "KT_MODE", "demo")
    app = create_app()
    client = TestClient(app)
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/creator"

    # In real mode, root redirects to editor
    monkeypatch.setattr(settings, "KT_MODE", "real")
    app_real = create_app()
    client_real = TestClient(app_real)
    response_real = client_real.get("/", follow_redirects=False)
    assert response_real.status_code in (302, 307)
    assert response_real.headers["location"] == "/editor"
