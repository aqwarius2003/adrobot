from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.core.config import settings
from src.core.security import (
    SESSION_COOKIE_NAME,
    SecurityStore,
    generate_csrf_token,
    get_client_ip,
    get_current_session,
    get_security_store,
    is_https_request,
    validate_safe_return_url,
    verify_csrf_token,
    verify_request_origin,
)
from src.services.auth_service import AuthService

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
mode_router = APIRouter(prefix="/api/auth", tags=["auth"])


def _verify_origin_and_csrf(request: Request, session: dict[str, Any] | None) -> None:
    verify_request_origin(request)
    if session:
        csrf_token = request.headers.get("x-csrf-token")
        if not csrf_token or not verify_csrf_token(csrf_token, session["token_hash"]):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid or missing CSRF token",
            )


class LoginRequest(BaseModel):
    api_key: str = Field(..., max_length=1024, description="Keitaro Admin API key")
    return_to: str | None = Field(default=None, max_length=2048, description="Target return URL")


class SwitchModeRequest(BaseModel):
    mode: str = Field(..., description="Target mode: 'real' or 'demo'")


def get_auth_service(store: SecurityStore = Depends(get_security_store)) -> AuthService:
    return AuthService(store)


@auth_router.post("/login")
async def login(
    req: LoginRequest,
    request: Request,
    response: Response,
    service: AuthService = Depends(get_auth_service),
) -> Any:
    """Authenticate with Keitaro API key and issue session."""
    client_ip = get_client_ip(request)

    # Production environments strictly require HTTPS for session authentication
    is_https = is_https_request(request)
    if settings.APP_ENV == "production" and not is_https:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": "HTTPS is strictly required for real mode in production"},
            headers={"Cache-Control": "no-store"},
        )

    result = await service.authenticate(req.api_key, client_ip)

    if result["success"]:
        resp = JSONResponse(
            content={
                "status": "ok",
                "authenticated": True,
                "csrf_token": result["csrf_token"],
                "return_to": validate_safe_return_url(req.return_to),
            },
            headers={"Cache-Control": "no-store"},
        )
        secure_cookie = True if settings.APP_ENV == "production" else is_https
        resp.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=result["token"],
            max_age=getattr(settings, "SESSION_MAX_LIFETIME_HOURS", 8) * 3600,
            httponly=True,
            samesite="strict",
            secure=secure_cookie,
            path="/",
        )
        # Also set adrobot_mode to real
        resp.set_cookie(
            key="adrobot_mode",
            value="real",
            max_age=getattr(settings, "SESSION_MAX_LIFETIME_HOURS", 8) * 3600,
            httponly=False,
            samesite="lax",
            secure=secure_cookie,
            path="/",
        )
        return resp

    status_code = result.get("status_code", 401)
    headers = {"Cache-Control": "no-store"}
    if status_code == 429:
        retry_after = result.get("retry_after", 120)
        headers["Retry-After"] = str(retry_after)
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "detail": result["detail"],
                "retry_after_seconds": retry_after,
            },
            headers=headers,
        )

    return JSONResponse(
        status_code=status_code,
        content={"detail": result["detail"]},
        headers=headers,
    )


@auth_router.post("/logout")
async def logout(
    request: Request,
    store: SecurityStore = Depends(get_security_store),
    session: dict[str, Any] | None = Depends(get_current_session),
) -> Any:
    """Revoke current real session and clear session cookies."""
    _verify_origin_and_csrf(request, session)
    if session:
        store.revoke_session(session["token_hash"])

    resp = JSONResponse(
        content={"status": "ok", "authenticated": False}, headers={"Cache-Control": "no-store"}
    )
    resp.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return resp


@auth_router.get("/session")
async def get_session_status(
    session: dict[str, Any] | None = Depends(get_current_session),
) -> Any:
    """Return safe session authentication status and CSRF token (no credentials exposed)."""
    if session:
        csrf = generate_csrf_token(session["token_hash"])
        return JSONResponse(
            content={
                "authenticated": True,
                "mode": "real",
                "csrf_token": csrf,
                "expires_at": session["expires_at"],
            },
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(
        content={
            "authenticated": False,
            "mode": "demo" if settings.DEMO_ENABLED else "real",
            "csrf_token": None,
            "expires_at": None,
        },
        headers={"Cache-Control": "no-store"},
    )


@mode_router.post("/switch-mode")
async def switch_mode(
    req: SwitchModeRequest,
    request: Request,
    store: SecurityStore = Depends(get_security_store),
    session: dict[str, Any] | None = Depends(get_current_session),
) -> Any:
    """Switch connection mode.

    real -> demo: revokes real session, deletes session cookie, sets mode cookie.
    demo -> real: requires valid active session or returns 401.
    """
    if not settings.DEMO_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")
    _verify_origin_and_csrf(request, session)
    clean_mode = req.mode.strip().lower()
    if clean_mode not in ("real", "demo"):
        return JSONResponse(status_code=422, content={"detail": f"Invalid mode '{req.mode}'"})

    is_https = is_https_request(request)

    if clean_mode == "demo":
        if session:
            store.revoke_session(session["token_hash"])
        resp = JSONResponse(content={"status": "ok", "mode": "demo", "authenticated": False})
        resp.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
        resp.set_cookie(
            key="adrobot_mode",
            value="demo",
            max_age=86400 * 30,
            httponly=False,
            samesite="lax",
            secure=is_https,
            path="/",
        )
        return resp
    else:
        if not session:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Authentication required for real Keitaro mode"},
            )
        resp = JSONResponse(content={"status": "ok", "mode": "real", "authenticated": True})
        resp.set_cookie(
            key="adrobot_mode",
            value="real",
            max_age=86400 * 30,
            httponly=False,
            samesite="lax",
            secure=is_https,
            path="/",
        )
        return resp
