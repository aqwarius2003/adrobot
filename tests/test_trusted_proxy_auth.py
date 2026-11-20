import asyncio

import pytest
from fastapi import HTTPException, Request, Response

from src.api.routes_auth import (
    LoginRequest,
    SwitchModeRequest,
    _verify_origin_and_csrf,
    login,
    switch_mode,
)
from src.core import security
from src.core.config import settings

PUBLIC_HOST = "8000-machine.instabox.tech"
GATEWAY = "172.19.0.1"


def proxy_request(*, peer=GATEWAY, origin=None, referer=None, extra=None):
    headers = {
        "host": "127.0.0.1:8000",
        "x-forwarded-host": PUBLIC_HOST,
        "x-forwarded-proto": "https",
    }
    if origin is not None:
        headers["origin"] = origin
    if referer is not None:
        headers["referer"] = referer
    headers.update(extra or {})
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/api/auth/switch-mode",
            "query_string": b"",
            "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
            "client": (peer, 12345),
            "server": ("127.0.0.1", 8000),
        }
    )


@pytest.fixture(autouse=True)
def trusted_gateway(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", [GATEWAY])


def test_same_public_origin_can_reach_authentication_gate():
    request = proxy_request(origin=f"https://{PUBLIC_HOST}")
    response = asyncio.run(switch_mode(SwitchModeRequest(mode="real"), request, None, None))
    assert response.status_code == 401


def test_public_referer_passes_for_trusted_proxy():
    _verify_origin_and_csrf(proxy_request(referer=f"https://{PUBLIC_HOST}/demo/editor"), None)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"origin": "https://attacker.example"},
        {"origin": f"https://{PUBLIC_HOST}", "peer": "198.51.100.10"},
        {
            "origin": f"https://{PUBLIC_HOST}",
            "extra": {"x-forwarded-host": f"{PUBLIC_HOST},attacker.example"},
        },
    ],
)
def test_cross_origin_or_untrusted_forwarding_stays_rejected(kwargs):
    with pytest.raises(HTTPException) as error:
        _verify_origin_and_csrf(proxy_request(**kwargs), None)
    assert error.value.status_code == 403


def test_real_mutation_keeps_csrf_check_behind_proxy(monkeypatch):
    session = {"token_hash": "synthetic-session"}
    monkeypatch.setattr(security, "verify_csrf_token", lambda token, _: token == "synthetic-csrf")
    request = proxy_request(
        origin=f"https://{PUBLIC_HOST}", extra={"x-csrf-token": "synthetic-csrf"}
    )
    security.require_csrf(request, session)
    with pytest.raises(HTTPException) as error:
        security.require_csrf(proxy_request(origin=f"https://{PUBLIC_HOST}"), session)
    assert error.value.status_code == 403
    assert "CSRF" in error.value.detail


def test_switch_cookie_is_secure_behind_trusted_https_proxy():
    request = proxy_request(origin=f"https://{PUBLIC_HOST}")
    response = asyncio.run(switch_mode(SwitchModeRequest(mode="demo"), request, None, None))
    mode_cookie = next(
        h for h in response.headers.getlist("set-cookie") if h.startswith("adrobot_mode=")
    )
    assert "Secure" in mode_cookie


def test_direct_localhost_origin_still_works():
    request = proxy_request(
        peer="127.0.0.1",
        origin="http://127.0.0.1:8000",
        extra={"x-forwarded-host": "ignored.example"},
    )
    _verify_origin_and_csrf(request, None)


@pytest.mark.parametrize("peer, expected_status", [(GATEWAY, 401), ("198.51.100.10", 403)])
def test_https_login_trusts_only_the_configured_immediate_proxy(monkeypatch, peer, expected_status):
    monkeypatch.setattr(settings, "APP_ENV", "production")

    class NoTrackerService:
        called = False

        async def authenticate(self, api_key, client_ip):
            self.called = True
            return {"success": False, "status_code": 401, "detail": "synthetic rejection"}

    service = NoTrackerService()
    response = asyncio.run(
        login(LoginRequest(api_key="synthetic"), proxy_request(peer=peer), Response(), service)
    )
    assert response.status_code == expected_status
    assert service.called is (peer == GATEWAY)
