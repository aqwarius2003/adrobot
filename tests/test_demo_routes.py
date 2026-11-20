"""Tests for Demo Mode API routes and UI badges."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from src.core.config import settings
from src.core.database import Base, engine
from src.main import app


@pytest.fixture(autouse=True)
def setup_tables():
    Base.metadata.create_all(bind=engine)
    yield


client = TestClient(app)


def test_health_endpoint_includes_kt_mode():
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert "kt_mode" in data
    assert data["kt_mode"] in ("real", "demo")


def test_demo_endpoints_forbidden_in_real_mode():
    settings.KT_MODE = "real"

    res_reset = client.post("/api/demo/reset")
    assert res_reset.status_code == 403
    assert "only available in demo mode" in res_reset.text

    res_del = client.post(
        "/api/demo/simulate-delete-stream-offer",
        json={"stream_id": 11, "offer_id": 105},
    )
    assert res_del.status_code == 403

    res_dis = client.post(
        "/api/demo/simulate-disable-global-offer",
        json={"offer_id": 105, "available": False},
    )
    assert res_dis.status_code == 403


@pytest.mark.asyncio
async def test_demo_endpoints_allowed_in_demo_mode():
    settings.KT_MODE = "demo"
    try:
        mock_resp = httpx.Response(200, json={"status": "ok", "message": "mocked"})
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp

            # Reset
            res_reset = client.post("/api/demo/reset")
            assert res_reset.status_code == 200
            assert res_reset.json()["status"] == "ok"

            # Simulate delete
            res_del = client.post(
                "/api/demo/simulate-delete-stream-offer",
                json={"stream_id": 11, "offer_id": 105},
            )
            assert res_del.status_code == 200
            assert res_del.json()["status"] == "ok"

            # Simulate disable
            res_dis = client.post(
                "/api/demo/simulate-disable-global-offer",
                json={"offer_id": 105, "available": False},
            )
            assert res_dis.status_code == 200
            assert res_dis.json()["status"] == "ok"
    finally:
        settings.KT_MODE = "real"


def test_ui_templates_render_demo_badge_in_demo_mode():
    settings.KT_MODE = "demo"
    try:
        res = client.get("/demo/editor")
        assert res.status_code == 200
        assert "Демо · симулятор" in res.text
        assert "Keitaro — симулятор" in res.text
        config = json.loads(res.text.split('id="appConfigData">', 1)[1].split("</script>", 1)[0])
        assert config["is_demo"] is True

        res_cr = client.get("/demo/creator")
        assert res_cr.status_code == 200
        assert "Демо · симулятор" in res_cr.text
    finally:
        settings.KT_MODE = "real"


def test_ui_templates_render_real_status_in_real_mode():
    settings.KT_MODE = "real"
    from src.core.security import get_security_store

    store = get_security_store()
    token = store.create_session("real", key="synthetic-demo-fixture")
    res = client.get("/real/editor", cookies={"adrobot_session": token})
    assert res.status_code == 200
    assert "Подключение: Keitaro" in res.text
    assert "Keitaro — симулятор" not in res.text
    config = json.loads(res.text.split('id="appConfigData">', 1)[1].split("</script>", 1)[0])
    assert config["is_demo"] is False
