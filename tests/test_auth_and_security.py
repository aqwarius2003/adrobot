import hashlib
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from src.core.config import settings
from src.core.security import (
    SESSION_COOKIE_NAME,
    get_security_store,
    hash_token,
)
from src.main import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean_security_store():
    store = get_security_store()
    with store._get_connection() as conn:
        conn.execute("DELETE FROM security_sessions")
        conn.execute("DELETE FROM rate_limits")
        conn.commit()
    yield


def test_initial_visit_without_session_redirects_to_login(client: TestClient):
    """First visit to root / without session cookie redirects to /login."""
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 307
    assert res.headers["location"] == "/login"


def test_demo_editor_accessible_without_session(client: TestClient):
    """Demo editor requires no authentication."""
    client.cookies.set("adrobot_mode", "demo")
    res = client.get("/demo/editor")
    assert res.status_code == 200
    assert "Демо · симулятор" in res.text


def test_anonymous_real_api_returns_401_json(client: TestClient):
    """Direct anonymous access to /api/real/campaigns returns 401 JSON (not redirect)."""
    res = client.get("/api/real/campaigns")
    assert res.status_code == 401
    assert "Authentication required" in res.json()["detail"]


def test_forged_mode_cookie_does_not_grant_real_access(client: TestClient):
    """Forging adrobot_mode=real without valid adrobot_session is rejected with 401."""
    client.cookies.set("adrobot_mode", "real")
    res = client.get("/api/campaigns")
    assert res.status_code == 401
    assert "Authentication required" in res.json()["detail"]


def test_login_invalid_key_returns_401_and_increments_attempts(client: TestClient, monkeypatch):
    """Invalid API key returns 401 and records failed attempt."""
    monkeypatch.setattr(settings, "REAL_KEITARO_API_KEY", "secret-real-key-123")
    monkeypatch.setattr(settings, "REAL_KEITARO_BASE_URL", "https://tracker.real.com")
    with patch("httpx.AsyncClient.get", return_value=httpx.Response(401)):
        res = client.post("/api/auth/login", json={"api_key": "wrong-key-value"})
    assert res.status_code == 401
    assert "API-ключ не подходит или не даёт доступа к кампаниям" in res.json()["detail"]
    assert SESSION_COOKIE_NAME not in res.cookies


def test_login_success_with_valid_key_and_kt_check(client: TestClient, monkeypatch):
    """Valid API key + successful KT GET /admin_api/v1/campaigns returns 200 and session cookie."""
    mock_response = httpx.Response(200, json=[{"id": 1, "name": "Campaign 1"}])
    monkeypatch.setattr(settings, "REAL_KEITARO_API_KEY", "secret-real-key-123")
    monkeypatch.setattr(settings, "REAL_KEITARO_BASE_URL", "https://tracker.real.com")

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        res = client.post("/api/auth/login", json={"api_key": "secret-real-key-123"})
        assert res.status_code == 200
        data = res.json()
        assert data["authenticated"] is True
        assert "csrf_token" in data
        assert SESSION_COOKIE_NAME in res.cookies
        assert res.cookies["adrobot_mode"] == "real"


def test_rate_limiting_tiers_5_10_15_attempts(client: TestClient):
    """Brute-force protection:
    5 failed attempts -> 2 min block (120s)
    next 5 failed attempts -> 4 min block (240s)
    next 5 failed attempts -> 16 min block (960s max)
    """
    store = get_security_store()
    fake_ip = "192.168.1.100"

    t0 = 1000000.0

    # 1. First 4 failed attempts: not blocked
    for _ in range(4):
        tier, retry = store.record_failed_attempt(fake_ip, now=t0)
        assert retry == 0

    # 2. 5th failed attempt: Tier 1 block (120s)
    tier, retry = store.record_failed_attempt(fake_ip, now=t0)
    assert tier == 1
    assert retry == 120

    # Verify check_rate_limit reports blocked
    allowed, retry_check = store.check_rate_limit(fake_ip, now=t0 + 10)
    assert allowed is False
    assert retry_check == 110

    # 3. Simulate passage of 121 seconds (block expired)
    t1 = t0 + 121
    allowed, _ = store.check_rate_limit(fake_ip, now=t1)
    assert allowed is True

    # Next 4 attempts
    for _ in range(4):
        tier, retry = store.record_failed_attempt(fake_ip, now=t1)
        assert retry == 0

    # 10th attempt total: Tier 2 block (240s)
    tier, retry = store.record_failed_attempt(fake_ip, now=t1)
    assert tier == 2
    assert retry == 240

    # 4. Simulate passage of 241 seconds
    t2 = t1 + 241
    allowed, _ = store.check_rate_limit(fake_ip, now=t2)
    assert allowed is True

    # Next 4 attempts
    for _ in range(4):
        tier, retry = store.record_failed_attempt(fake_ip, now=t2)
        assert retry == 0

    # 15th attempt: Tier 3 block (960s)
    tier, retry = store.record_failed_attempt(fake_ip, now=t2)
    assert tier == 3
    assert retry == 960

    # 5. Next attempts do not exceed 960s max
    t3 = t2 + 961
    for _ in range(5):
        tier, retry = store.record_failed_attempt(fake_ip, now=t3)
    assert tier == 4
    assert retry == 960


def test_kt_timeout_returns_503_and_does_not_count_as_failed_password(
    client: TestClient, monkeypatch
):
    """KT network timeout returns 503 and does not increment failed brute-force counter."""
    store = get_security_store()
    monkeypatch.setattr(settings, "REAL_KEITARO_API_KEY", "secret-real-key-123")
    monkeypatch.setattr(settings, "REAL_KEITARO_BASE_URL", "https://tracker.real.com")

    with patch("httpx.AsyncClient.get", side_effect=httpx.TimeoutException("Connection timed out")):
        res = client.post("/api/auth/login", json={"api_key": "secret-real-key-123"})
        assert res.status_code == 503
        assert "Не удалось проверить подключение" in res.json()["detail"]

    # Rate limits table must still have 0 failed attempts
    allowed, retry = store.check_rate_limit("testclient")
    assert allowed is True
    assert retry == 0


def test_switch_real_to_demo_revokes_session(client: TestClient, monkeypatch):
    """Switching real -> demo terminates session on server and removes cookie."""
    store = get_security_store()
    mock_response = httpx.Response(200, json=[{"id": 1}])
    monkeypatch.setattr(settings, "REAL_KEITARO_API_KEY", "secret-real-key-123")
    monkeypatch.setattr(settings, "REAL_KEITARO_BASE_URL", "https://tracker.real.com")

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        # 1. Login
        login_res = client.post("/api/auth/login", json={"api_key": "secret-real-key-123"})
        assert login_res.status_code == 200
        token = login_res.cookies[SESSION_COOKIE_NAME]
        csrf_token = login_res.json()["csrf_token"]

    # Verify session is active
    token_h = hash_token(token)
    assert (
        store.get_valid_session(token_h, hashlib.sha256(b"secret-real-key-123").hexdigest())
        is not None
    )

    # 2. Switch to demo with CSRF token
    switch_res = client.post(
        "/api/auth/switch-mode",
        json={"mode": "demo"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert switch_res.status_code == 200
    assert switch_res.json()["mode"] == "demo"
    assert SESSION_COOKIE_NAME not in switch_res.cookies

    # 3. Old session token is revoked on server
    assert (
        store.get_valid_session(token_h, hashlib.sha256(b"secret-real-key-123").hexdigest()) is None
    )


def test_csrf_protection_on_mutating_real_requests(client: TestClient, monkeypatch):
    """Mutating real request without valid X-CSRF-Token returns 403 Forbidden."""
    mock_response = httpx.Response(200, json=[{"id": 1}])
    monkeypatch.setattr(settings, "REAL_KEITARO_API_KEY", "secret-real-key-123")
    monkeypatch.setattr(settings, "REAL_KEITARO_BASE_URL", "https://tracker.real.com")

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        login_res = client.post("/api/auth/login", json={"api_key": "secret-real-key-123"})
        csrf_token = login_res.json()["csrf_token"]

    # 1. Mutating POST without CSRF token -> 403
    res_no_csrf = client.post(
        "/api/real/campaigns", json={"name": "test", "geo": "KZ", "offer_ids": [101]}
    )
    assert res_no_csrf.status_code == 403
    assert "CSRF" in res_no_csrf.json()["detail"]

    # A controlled validation error proves valid CSRF reaches the creator without network writes.
    async def validate_only(self, request):
        raise ValueError("creator-validation-reached")

    monkeypatch.setattr(
        "src.services.creator_service.CampaignCreatorService.create_campaign", validate_only
    )
    res_with_csrf = client.post(
        "/api/real/campaigns",
        json={"name": "test", "geo": "KZ", "offer_ids": [101]},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert res_with_csrf.status_code == 422
    assert res_with_csrf.json()["detail"] == "creator-validation-reached"


def test_simulator_put_stream_strict_validation():
    """Simulator PUT /admin_api/v1/streams/{id} validates integer shares, sum 100%, and Api-Key."""
    from src.simulator.app import simulator_app

    with TestClient(simulator_app) as sim_client:
        # 1. Missing or wrong Api-Key -> 401
        res_auth = sim_client.put(
            "/admin_api/v1/streams/11",
            json={"offers": [{"offer_id": 101, "share": 100}]},
            headers={"Api-Key": "wrong-synthetic-key"},
        )
        assert res_auth.status_code == 401

        # 2. Invalid shares sum != 100% -> 422
        res_shares = sim_client.put(
            "/admin_api/v1/streams/11",
            json={"offers": [{"offer_id": 101, "share": 60}, {"offer_id": 102, "share": 30}]},
            headers={"Api-Key": "demo-api-key"},
        )
        assert res_shares.status_code == 422
        assert "Sum of active shares must be strictly 100%" in str(res_shares.json())

        # 3. Duplicate offer_ids -> 422
        res_dup = sim_client.put(
            "/admin_api/v1/streams/11",
            json={"offers": [{"offer_id": 101, "share": 50}, {"offer_id": 101, "share": 50}]},
            headers={"Api-Key": "demo-api-key"},
        )
        assert res_dup.status_code == 422
        assert "Duplicate offer_ids" in str(res_dup.json())

        # 4. Valid payload -> 200
        res_valid = sim_client.put(
            "/admin_api/v1/streams/11",
            json={"offers": [{"offer_id": 101, "share": 50}, {"offer_id": 102, "share": 50}]},
            headers={"Api-Key": "demo-api-key"},
        )
        assert res_valid.status_code == 200
        assert len(res_valid.json()["offers"]) == 2


def test_malformed_auth_body_does_not_leak_key_in_error(client: TestClient):
    """Malformed body or validation error must not reflect raw api_key input back to client."""
    secret_marker = "SUPER_SECRET_VALUE_DO_NOT_REFLECT_12345"
    res = client.post("/api/auth/login", json={"api_key": ["invalid_type", secret_marker]})
    assert res.status_code == 422
    # Ensure sensitive payload isn't leaked in details
    assert secret_marker not in res.text

    # Too long key
    res_long = client.post("/api/auth/login", json={"api_key": "x" * 2000})
    assert res_long.status_code in (401, 422)
    assert "x" * 1500 not in res_long.text


def test_cross_origin_mutating_request_rejected(client: TestClient, monkeypatch):
    """Mutating real request with untrusted Origin header is rejected with 403."""
    mock_response = httpx.Response(200, json=[{"id": 1}])
    monkeypatch.setattr(settings, "REAL_KEITARO_API_KEY", "secret-real-key-123")
    monkeypatch.setattr(settings, "REAL_KEITARO_BASE_URL", "https://tracker.real.com")

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        login_res = client.post("/api/auth/login", json={"api_key": "secret-real-key-123"})
        csrf_token = login_res.json()["csrf_token"]

    res = client.post(
        "/api/real/campaigns",
        json={"name": "test", "geo": "KZ", "offer_ids": [101]},
        headers={"X-CSRF-Token": csrf_token, "Origin": "https://attacker.example.com"},
    )
    assert res.status_code == 403
    assert "Origin mismatch" in res.json()["detail"]


def test_r1_full_csrf_flow_editor_actions(client: TestClient, monkeypatch):
    """R1: Safe CSRF client transport: login yields csrf_token, mutating calls require it."""
    mock_response = httpx.Response(200, json=[{"id": 1}])
    monkeypatch.setattr(settings, "REAL_KEITARO_API_KEY", "secret-real-key-123")
    monkeypatch.setattr(settings, "REAL_KEITARO_BASE_URL", "https://tracker.real.com")

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        login_res = client.post("/api/auth/login", json={"api_key": "secret-real-key-123"})
        assert login_res.status_code == 200
        csrf_token = login_res.json()["csrf_token"]

    # 1. Mutating call without CSRF header is rejected with 403
    no_csrf = client.post(
        "/api/auth/logout",
    )
    assert no_csrf.status_code == 403
    assert "CSRF" in no_csrf.json()["detail"]

    # 2. Mutating call with CSRF header succeeds
    with_csrf = client.post(
        "/api/auth/logout",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert with_csrf.status_code == 200


def test_r4_safe_return_url_validation(client: TestClient):
    """R4: return_to parameter must reject open redirects and javascript: schemes."""
    from src.core.security import validate_safe_return_url

    # Malicious URLs must fallback to /real/editor
    assert validate_safe_return_url("javascript:alert(1)") == "/real/editor"
    assert validate_safe_return_url("javascript:void(0)") == "/real/editor"
    assert validate_safe_return_url("//evil.com") == "/real/editor"
    assert validate_safe_return_url(r"\\evil.com") == "/real/editor"
    assert validate_safe_return_url("https://attacker.com") == "/real/editor"
    assert validate_safe_return_url("http://attacker.com") == "/real/editor"
    assert validate_safe_return_url("/\\evil.com") == "/real/editor"

    # Legitimate internal paths are preserved
    assert validate_safe_return_url("/real/editor") == "/real/editor"
    assert validate_safe_return_url("/editor") == "/editor"
    assert validate_safe_return_url("/real/editor?camp=5") == "/real/editor?camp=5"
    assert validate_safe_return_url("/demo/editor") == "/real/editor"

    # Visiting /login with bad return_to sets safe return_to
    res = client.get("/login?return_to=javascript:alert(1)")
    assert res.status_code == 200
    assert 'value="/real/editor"' in res.text
    assert "javascript:alert(1)" not in res.text


def test_r5_concurrent_failed_attempts_do_not_escalate_tier_during_active_block(
    client: TestClient, monkeypatch
):
    """R5: Concurrent failed attempts during active block must not escalate tier or extend block."""
    import concurrent.futures

    store = get_security_store()
    monkeypatch.setattr(settings, "REAL_KEITARO_API_KEY", "secret-real-key-123")
    monkeypatch.setattr(settings, "REAL_KEITARO_BASE_URL", "https://tracker.real.com")
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", ["testclient", "127.0.0.1"])

    # Send 10 concurrent requests with wrong password
    def attempt_login(i):
        return client.post(
            "/api/auth/login",
            json={"api_key": f"wrong-key-{i}"},
            headers={"X-Forwarded-For": "198.51.100.42"},
        )

    with (
        patch("httpx.AsyncClient.get", return_value=httpx.Response(401)),
        concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor,
    ):
        results = list(executor.map(attempt_login, range(10)))

    # At least some should be 401, followed by 429 once blocked
    status_codes = [r.status_code for r in results]
    assert 401 in status_codes
    assert 429 in status_codes

    # Check that rate limit tier is strictly 1 (120s), not escalated to tier 2 or 3!
    allowed, retry = store.check_rate_limit("198.51.100.42")
    assert allowed is False
    assert 0 < retry <= 120


def test_r5_trusted_proxy_forwarded_for_resolution(client: TestClient, monkeypatch):
    """R5: Resolve client IP safely via trusted proxy chain."""
    from src.core.security import get_client_ip

    monkeypatch.setattr(settings, "TRUSTED_PROXIES", ["10.0.0.1", "127.0.0.1"])

    from starlette.datastructures import Headers

    class DummyReq:
        def __init__(self, headers, peer):
            self.headers = Headers(headers)
            self.client = type("Client", (), {"host": peer})()

    # Trusted proxy: resolves true client IP
    req1 = DummyReq({"X-Forwarded-For": "203.0.113.195, 10.0.0.1"}, "10.0.0.1")
    assert get_client_ip(req1) == "203.0.113.195"

    # Untrusted proxy in chain stops at rightmost untrusted
    req2 = DummyReq({"X-Forwarded-For": "1.1.1.1, 203.0.113.195"}, "10.0.0.1")
    assert get_client_ip(req2) == "203.0.113.195"


def test_r6_direct_demo_navigation_prompts_confirmation(client: TestClient, monkeypatch):
    """R6: Direct visits to /demo/simulator and /demo/creator prompt confirmation when real session is active."""
    mock_response = httpx.Response(200, json=[{"id": 1}])
    monkeypatch.setattr(settings, "REAL_KEITARO_API_KEY", "secret-real-key-123")
    monkeypatch.setattr(settings, "REAL_KEITARO_BASE_URL", "https://tracker.real.com")

    # 1. Direct visit without session: simulator loads directly
    res_anon_sim = client.get("/demo/simulator")
    assert res_anon_sim.status_code == 200
    assert "симулятор" in res_anon_sim.text

    # 2. Login to real mode
    with patch("httpx.AsyncClient.get", return_value=mock_response):
        login_res = client.post("/api/auth/login", json={"api_key": "secret-real-key-123"})
        assert login_res.status_code == 200

    # 3. Direct visit with active real session: shows confirmation warning
    res_warn_sim = client.get("/demo/simulator")
    assert res_warn_sim.status_code == 200
    assert "Переход в демонстрационный режим" in res_warn_sim.text

    res_warn_creator = client.get("/demo/creator")
    assert res_warn_creator.status_code == 200
    assert "Переход в демонстрационный режим" in res_warn_creator.text


def test_r7_production_readiness_and_security(client: TestClient, monkeypatch):
    """R7: Production readiness checks reject dev secret and insecure HTTP; real HTML sets no-store."""
    mock_response = httpx.Response(200, json=[{"id": 1}])
    monkeypatch.setattr(settings, "REAL_KEITARO_API_KEY", "secret-real-key-123")
    monkeypatch.setattr(settings, "REAL_KEITARO_BASE_URL", "https://tracker.real.com")
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", ["testclient", "127.0.0.1"])

    # 1. Dev secret in production -> 503
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "dev-secret-key-change-in-prod")

    res_dev_secret = client.post(
        "/api/auth/login",
        json={"api_key": "secret-real-key-123"},
        headers={"X-Forwarded-Proto": "https"},
    )
    assert res_dev_secret.status_code == 503
    assert "Не удалось проверить подключение" in res_dev_secret.json()["detail"]

    # 2. Insecure HTTP in production -> 403
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "a" * 64)
    res_http = client.post(
        "/api/auth/login",
        json={"api_key": "secret-real-key-123"},
        headers={"X-Forwarded-Proto": "http"},
    )
    assert res_http.status_code == 403
    assert "HTTPS is strictly required" in res_http.json()["detail"]

    # 3. HTTPS in production sets secure=True cookie
    with patch("httpx.AsyncClient.get", return_value=mock_response):
        res_https = client.post(
            "/api/auth/login",
            json={"api_key": "secret-real-key-123"},
            headers={"X-Forwarded-Proto": "https"},
        )
        assert res_https.status_code == 200
        cookie_header = res_https.headers.get("set-cookie", "")
        assert "Secure" in cookie_header

    # 4. GET /real/editor sets Cache-Control: no-store
    res_editor = client.get("/real/editor")
    assert res_editor.status_code == 200
    assert "no-store" in res_editor.headers.get("cache-control", "")


def test_r12_root_body_malformed_key_leakage_prevented(client: TestClient):
    """R12: Malformed root or nested payloads do not reflect secret values in 422 errors."""
    secret_marker_1 = "DO_NOT_LEAK_ROOT_ARRAY_99999"
    res1 = client.post("/api/auth/login", json=[{"api_key": secret_marker_1}])
    assert res1.status_code == 422
    assert secret_marker_1 not in res1.text

    secret_marker_2 = "DO_NOT_LEAK_NESTED_DICT_88888"
    res2 = client.post("/api/auth/login", json={"api_key": {"secret": secret_marker_2}})
    assert res2.status_code == 422
    assert secret_marker_2 not in res2.text


def test_csp_compatibility_zero_inline_executable_scripts(client: TestClient, monkeypatch):
    """R1/R6: Verify that CSP enforces script-src 'self' without unsafe-inline, and templates have zero inline scripts."""
    import re

    mock_response = httpx.Response(200, json=[{"id": 1}])
    monkeypatch.setattr(settings, "REAL_KEITARO_API_KEY", "secret-real-key-123")
    monkeypatch.setattr(settings, "REAL_KEITARO_BASE_URL", "https://tracker.real.com")

    # 1. Login to real mode
    with patch("httpx.AsyncClient.get", return_value=mock_response):
        login_res = client.post("/api/auth/login", json={"api_key": "secret-real-key-123"})
        assert login_res.status_code == 200

    pages_to_check = [
        "/login",
        "/real/editor",
        "/demo/editor",
        "/demo/simulator",  # prompts confirm_switch_demo.html when real session active
    ]

    inline_script_pattern = re.compile(
        r"<script(?![^>]*\b(src=|type=[\"']application/json[\"']))[^>]*>", re.IGNORECASE
    )

    for path in pages_to_check:
        res = client.get(path)
        assert res.status_code == 200

        csp = res.headers.get("content-security-policy", "")
        assert "script-src 'self'" in csp
        script_src = csp.split("script-src")[1].split(";")[0]
        assert "unsafe-inline" not in script_src

        # Assert no executable inline script tags exist in the rendered HTML
        matches = inline_script_pattern.findall(res.text)
        assert len(matches) == 0, f"Found executable inline script tag in {path}: {matches}"
