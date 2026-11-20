from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from src.core.config import settings
from src.core.connection_context import build_connection_context

router = APIRouter(prefix="/api/session", tags=["Session"])


class ModeUpdateRequest(BaseModel):
    mode: str


@router.get("/context")
def get_session_context(request: Request):
    """Retrieve current connection mode context and tracker availability."""
    from src.core.security import get_current_session, get_security_store

    session = get_current_session(request, store=get_security_store())
    cookie_mode = request.cookies.get("adrobot_mode")

    if cookie_mode == "demo":
        mode = "demo"
    elif session:
        mode = "real"
    elif cookie_mode == "real":
        mode = "real"
    else:
        mode = settings.KT_MODE if settings.KT_MODE in ("real", "demo") else "real"

    ctx = build_connection_context(mode, api_key=session["_api_key"] if session else None)
    return {
        "mode": ctx.mode,
        "is_demo": ctx.is_demo,
        "base_url": ctx.base_url,
        "badge": "Демо · симулятор" if ctx.is_demo else "Подключение: Keitaro",
        "is_configured": ctx.is_configured,
        "authenticated": bool(session),
    }


@router.post("/context")
def set_session_context(payload: ModeUpdateRequest, request: Request, response: Response):
    """Update connection mode preference without modifying global process settings."""
    clean_mode = payload.mode.strip().lower()
    if clean_mode not in ("real", "demo"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid mode '{payload.mode}'. Must be either 'real' or 'demo'.",
        )

    from src.core.security import (
        SESSION_COOKIE_NAME,
        get_current_session,
        get_security_store,
        require_csrf,
    )

    store = get_security_store()
    session = get_current_session(request, store=store)

    if session:
        require_csrf(request, session=session)

    if clean_mode == "real":
        if not session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required for real Keitaro mode",
            )
    elif clean_mode == "demo":
        if session:
            store.revoke_session(session["token_hash"])
        response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")

    # Persist in session cookie
    response.set_cookie(
        key="adrobot_mode",
        value=clean_mode,
        max_age=86400 * 30,
        samesite="lax",
        httponly=False,
        path="/",
    )

    ctx = build_connection_context(clean_mode, api_key=session["_api_key"] if session else None)
    return {
        "status": "ok",
        "mode": clean_mode,
        "is_demo": ctx.is_demo,
        "badge": "Демо · симулятор" if ctx.is_demo else "Подключение: Keitaro",
        "is_configured": ctx.is_configured,
        "authenticated": bool(clean_mode == "real" and session),
    }
