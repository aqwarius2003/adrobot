from fastapi.testclient import TestClient

from src.main import create_app


def test_ui_creator_page():
    app = create_app()
    client = TestClient(app)
    response = client.get("/creator")
    assert response.status_code == 200
    assert "Создание кампании" in response.text
    assert "test_novikov_" in response.text
    assert "routing-preview-box" in response.text


def test_ui_editor_page():
    app = create_app()
    client = TestClient(app)
    response = client.get("/editor")
    assert response.status_code == 200
    assert "Редактор кампаний" in response.text
    assert "Fetch Streams" in response.text
    assert "streamsContainer" in response.text


def test_root_redirects_to_creator():
    app = create_app()
    client = TestClient(app)
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/creator"
