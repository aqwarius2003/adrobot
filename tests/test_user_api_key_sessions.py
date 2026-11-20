import hashlib
from pathlib import Path

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.api.routes_auth import auth_router
from src.api.routes_campaigns import router as campaigns_router
from src.api.routes_streams import router as streams_router
from src.core import security
from src.core.config import settings
from src.core.connection_context import ConnectionContext, get_connection_context
from src.domain.models import Operation

KEY_A = "synthetic-reviewer-key-a"
KEY_B = "synthetic-reviewer-key-b"


@pytest.fixture
def auth_app(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(
        settings, "APP_SECRET_KEY", "synthetic-test-app-secret-at-least-32-characters"
    )
    monkeypatch.setattr(settings, "REAL_KEITARO_BASE_URL", "https://kt.example.test")
    monkeypatch.setattr(settings, "REAL_KEITARO_API_KEY", "different-server-key")
    monkeypatch.setattr(settings, "REAL_DATABASE_URL", f"sqlite:///{tmp_path / 'real.db'}")
    store = security.SecurityStore(f"sqlite:///{tmp_path / 'security.db'}")
    monkeypatch.setattr(security, "_security_store", store)
    requests = []

    def tracker(request):
        requests.append(request)
        reply = getattr(app.state, "tracker_reply", None)
        if isinstance(reply, Exception):
            raise reply
        if reply is not None:
            return reply
        key = request.headers.get("Api-Key")
        if key not in (KEY_A, KEY_B):
            return httpx.Response(401)
        assert request.url.host == "kt.example.test"
        return httpx.Response(
            200, json=[{"id": 11 if key == KEY_A else 22, "name": "Allowed campaign"}]
        )

    original_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(tracker)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(campaigns_router, prefix="/api/real")
    app.include_router(streams_router, prefix="/api/real")

    @app.get("/api/real/probe")
    def probe(context: ConnectionContext = Depends(get_connection_context)):
        with context.session_factory() as db:
            operation = db.query(Operation).first()
            return {"marker": operation.idempotency_key if operation else None}

    return app, store, requests


def login(client, key):
    result = client.post("/api/auth/login", json={"api_key": key})
    assert result.status_code == 200
    return result


def test_another_valid_key_is_accepted_and_used_for_real_requests(auth_app):
    app, _, requests = auth_app
    with TestClient(app) as client:
        response = login(client, KEY_A)
        assert KEY_A not in response.text
        assert all(KEY_A not in value for value in response.cookies.values())
        campaigns = client.get("/api/real/campaigns")
        assert campaigns.status_code == 200
        assert campaigns.json()[0]["id"] == 11
        assert len(requests) == 2
        assert all(r.headers["Api-Key"] == KEY_A for r in requests)


def test_env_key_is_not_required_or_used_as_fallback(auth_app, monkeypatch):
    app, _, requests = auth_app
    monkeypatch.setattr(settings, "REAL_KEITARO_API_KEY", "")
    monkeypatch.setattr(settings, "KEITARO_API_KEY", "")
    with TestClient(app) as client:
        login(client, KEY_B)
        assert client.get("/api/real/campaigns").json()[0]["id"] == 22
    assert all(r.headers["Api-Key"] == KEY_B for r in requests)


def test_two_keys_have_isolated_local_operations(auth_app, monkeypatch):
    app, store, _ = auth_app
    with TestClient(app) as a, TestClient(app) as b:
        login(a, KEY_A)
        login(b, KEY_B)
        token = a.cookies[security.SESSION_COOKIE_NAME]
        request = security.Request(
            {
                "type": "http",
                "headers": [(b"cookie", f"adrobot_session={token}".encode())],
                "path": "/api/real/probe",
                "scheme": "http",
                "method": "GET",
                "query_string": b"",
            }
        )
        context_a = get_connection_context(request)
        with context_a.session_factory() as db:
            operation = Operation(idempotency_key="private-to-key-a", operation_type="probe")
            db.add(operation)
            db.commit()
            operation_id = operation.id
        assert a.get("/api/real/probe").json()["marker"] == "private-to-key-a"
        assert b.get("/api/real/probe").json()["marker"] is None
        assert b.get(f"/api/real/operations/{operation_id}").status_code == 404
        assert a.get(f"/api/real/operations/{operation_id}").status_code == 200
        monkeypatch.setattr(security, "_security_store", security.SecurityStore(store.db_url))
        login(a, KEY_A)
        assert a.get("/api/real/probe").json()["marker"] == "private-to-key-a"
        # API keys remain absent from persistent SQLite data and scoped filenames.
        for file in Path(store._db_path).parent.rglob("*"):
            if file.is_file():
                assert KEY_A not in file.name and KEY_B not in file.name
                assert KEY_A.encode() not in file.read_bytes()
                assert KEY_B.encode() not in file.read_bytes()


def test_rejected_key_counts_failures_and_does_not_create_session(auth_app):
    app, store, requests = auth_app
    with TestClient(app) as client:
        for _ in range(4):
            assert (
                client.post("/api/auth/login", json={"api_key": "synthetic-invalid"}).status_code
                == 401
            )
        blocked = client.post("/api/auth/login", json={"api_key": "synthetic-invalid"})
        assert blocked.status_code == 429
        assert client.post("/api/auth/login", json={"api_key": KEY_A}).status_code == 429
        assert len(requests) == 5
        assert security.SESSION_COOKIE_NAME not in client.cookies


def test_restart_requires_login_again_without_falling_back_to_env(auth_app, monkeypatch):
    app, store, _ = auth_app
    with TestClient(app) as client:
        login(client, KEY_A)
        monkeypatch.setattr(security, "_security_store", security.SecurityStore(store.db_url))
        assert client.get("/api/real/campaigns").status_code == 401


def test_changing_server_key_does_not_change_authenticated_identity(auth_app, monkeypatch):
    app, _, _ = auth_app
    with TestClient(app) as client:
        login(client, KEY_A)
        monkeypatch.setattr(settings, "REAL_KEITARO_API_KEY", "another-env-key")
        assert client.get("/api/real/campaigns").json()[0]["id"] == 11


def test_logout_revokes_session_and_its_upstream_credential(auth_app):
    app, store, _ = auth_app
    with TestClient(app) as client:
        response = login(client, KEY_A)
        token = client.cookies[security.SESSION_COOKIE_NAME]
        result = client.post(
            "/api/auth/logout", headers={"X-CSRF-Token": response.json()["csrf_token"]}
        )
        assert result.status_code == 200
        assert (
            store.get_valid_session(
                security.hash_token(token), hashlib.sha256(KEY_A.encode()).hexdigest()
            )
            is None
        )
        client.cookies.set(security.SESSION_COOKIE_NAME, token)
        assert client.get("/api/real/campaigns").status_code == 401


def test_html_200_does_not_authenticate_and_timeout_does_not_count_failure(auth_app):
    app, store, _ = auth_app
    with TestClient(app) as client:
        app.state.tracker_reply = httpx.Response(200, text="<html>not an API response</html>")
        assert client.post("/api/auth/login", json={"api_key": KEY_A}).status_code == 503
        app.state.tracker_reply = httpx.ConnectTimeout("synthetic connection timeout")
        assert client.post("/api/auth/login", json={"api_key": KEY_A}).status_code == 503
        assert security.SESSION_COOKIE_NAME not in client.cookies
        with store._get_connection() as conn:
            assert conn.execute("SELECT failed_attempts FROM rate_limits").fetchone() is None


def test_user_key_is_redacted_from_upstream_error(auth_app, caplog):
    app, _, _ = auth_app
    with TestClient(app) as client:
        login(client, KEY_A)
        app.state.tracker_reply = httpx.Response(500, text=f"Synthetic diagnostic with {KEY_A}")
        error = client.get("/api/real/campaigns")
        assert error.status_code == 502
        assert KEY_A not in error.text
        assert KEY_A not in caplog.text
