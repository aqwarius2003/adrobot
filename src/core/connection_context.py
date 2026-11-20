from dataclasses import dataclass, field
from typing import Literal

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from src.core.config import (
    normalize_tracker_url,
    settings,
    validate_database_isolation,
    validate_upstream_isolation,
)
from src.core.database import get_session_factory


@dataclass(frozen=True)
class ConnectionContext:
    """Immutable per-request connection context encapsulating tracker and database bindings.

    Ensures that real mode and demo mode never mix credentials, databases, or client sessions.
    """

    mode: Literal["real", "demo"]
    base_url: str
    api_key: str = field(repr=False)
    database_url: str
    session_factory: sessionmaker[Session]
    is_demo: bool

    @property
    def is_configured(self) -> bool:
        if self.is_demo:
            return True
        return bool(self.api_key and self.base_url and "tracker.example" not in self.base_url)


def build_connection_context(
    mode: str, *, api_key: str | None = None, credential_scope: str | None = None
) -> ConnectionContext:
    clean_mode = (mode or "").strip().lower()
    if clean_mode not in ("real", "demo"):
        raise ValueError(f"Invalid connection mode: '{mode}'. Must be 'real' or 'demo'.")

    # Validate isolation between configured real and demo resources
    real_db = settings.get_real_database_url()
    demo_db = settings.get_demo_database_url()
    validate_database_isolation(real_db, demo_db)

    real_tracker = settings.get_real_keitaro_base_url()
    demo_tracker = settings.get_demo_keitaro_base_url()
    validate_upstream_isolation(real_tracker, demo_tracker)

    if clean_mode == "demo":
        base_url = normalize_tracker_url(demo_tracker)
        api_key = settings.get_demo_keitaro_api_key()
        db_url = demo_db
        factory = get_session_factory("demo")
        is_demo = True
    else:
        base_url = normalize_tracker_url(real_tracker)
        api_key = settings.get_real_keitaro_api_key() if api_key is None else api_key
        db_url = real_db
        factory = get_session_factory("real")
        if credential_scope:
            from src.core.credential_databases import get_credential_database

            db_url, factory = get_credential_database(real_db, credential_scope)
        is_demo = False

    return ConnectionContext(
        mode=clean_mode,  # type: ignore[arg-type]
        base_url=base_url,
        api_key=api_key,
        database_url=db_url,
        session_factory=factory,
        is_demo=is_demo,
    )


def get_connection_context(request: Request) -> ConnectionContext:
    """Extract connection context from the request.

    Priority:
    1. URL path prefix (/api/real/, /real/, /api/demo/, /demo/)
    2. Session / Cookie ('adrobot_mode')
    3. Startup configuration (settings.KT_MODE)
    """
    path = request.url.path
    if path.startswith("/api/real/") or path.startswith("/real/"):
        mode = "real"
    elif path.startswith("/api/demo/") or path.startswith("/demo/"):
        mode = "demo"
    else:
        cookie_mode = request.cookies.get("adrobot_mode")
        if cookie_mode in ("real", "demo"):
            mode = cookie_mode
        elif settings.KT_MODE in ("real", "demo"):
            mode = settings.KT_MODE
        else:
            mode = "real"

    if mode == "real":
        from fastapi import HTTPException, status

        from src.core.security import get_current_session, get_security_store, require_csrf

        session = get_current_session(request, store=get_security_store())
        if not session:
            is_api = path.startswith("/api/") or "application/json" in request.headers.get(
                "accept", ""
            )
            if is_api:
                # Allow public API endpoints without real auth
                if not path.startswith(("/api/auth/", "/api/options/countries", "/api/health")):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Authentication required for real Keitaro mode",
                    )
            else:
                if not path.startswith(("/login", "/static", "/health")):
                    from src.core.security import validate_safe_return_url

                    safe_return = validate_safe_return_url(path)
                    raise HTTPException(
                        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                        headers={"Location": f"/login?return_to={safe_return}"},
                    )

        # Mutating methods on real mode API require CSRF
        if session and request.method in ("POST", "PUT", "DELETE", "PATCH"):
            if path.startswith("/api/") and not path.startswith("/api/auth/"):
                require_csrf(request, session=session)

        if session:
            return build_connection_context(
                mode, api_key=session["_api_key"], credential_scope=session["_credential_scope"]
            )

    return build_connection_context(mode)
