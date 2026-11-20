import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from src.api.routes_auth import auth_router
from src.api.routes_campaigns import router as campaigns_router
from src.api.routes_context import router as context_router
from src.api.routes_health import router as health_router
from src.api.routes_streams import router as streams_router
from src.core.config import settings
from src.core.database import migrate_enabled_databases
from src.core.logging import logger, setup_logging
from src.web.routes_ui import router as ui_router


def recover_interrupted_streams(session_or_engine=None, now: float | None = None) -> int:
    """Recover genuinely crashed/orphaned sync operations whose worker lease has expired.

    Does not interrupt active operations leased by live peer workers.
    """
    import time

    from sqlalchemy.orm import Session

    from src.core.database import DemoSessionLocal, RealSessionLocal
    from src.domain.models import Operation, Stream

    now_ts = now if now is not None else time.time()
    recovered_count = 0
    if session_or_engine is not None:
        if hasattr(session_or_engine, "connect"):  # Engine
            factories = [lambda: Session(session_or_engine)]
        else:  # Session
            factories = [lambda: session_or_engine]
    else:
        factories = [RealSessionLocal]
        if settings.DEMO_ENABLED and DemoSessionLocal is not None:
            factories.append(DemoSessionLocal)

    for factory in factories:
        s_db = factory()
        try:
            stuck_streams = (
                s_db.query(Stream).filter(Stream.sync_status.in_(["publishing", "verifying"])).all()
            )
            for s in stuck_streams:
                last_op = (
                    s_db.query(Operation)
                    .filter_by(target_id=s.id, operation_type="push_stream")
                    .order_by(Operation.id.desc())
                    .first()
                )
                # Safe lease check: if live worker still holds an unexpired lease, do not interrupt
                if last_op and last_op.lease_expires_at and last_op.lease_expires_at > now_ts:
                    continue

                # Lease has expired or op missing: genuine prior process crash/restart
                s.sync_status = "unknown"
                recovered_count += 1
                if last_op and last_op.status == "in_progress":
                    last_op.status = "unknown"
                    last_op.error_message = (
                        f"Crash/restart detected: worker lease expired (worker={last_op.worker_id})"
                    )
            s_db.commit()
        finally:
            if session_or_engine is None or hasattr(session_or_engine, "connect"):
                s_db.close()
    return recovered_count


def check_simulator_health(sim_url: str, timeout: float = 0.8) -> bool:
    """Check if the Keitaro simulator service is running and healthy."""
    import json
    import urllib.request

    health_url = f"{sim_url.rstrip('/')}/health"
    try:
        req = urllib.request.Request(health_url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                body = resp.read().decode("utf-8")
                data = json.loads(body)
                return data.get("status") == "ok" and data.get("service") == "keitaro_simulator"
    except Exception:
        return False
    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize logger
    setup_logging()
    logger.info(
        "Starting Keitaro Campaign Manager (version=%s, env=%s, mode=%s)",
        settings.APP_VERSION,
        settings.APP_ENV,
        settings.default_mode,
    )

    # Auto-start local simulator on port 8001 if configured and not already running
    sim_server = None
    if settings.DEMO_ENABLED and (
        "127.0.0.1:8001" in settings.DEMO_SIMULATOR_URL
        or "localhost:8001" in settings.DEMO_SIMULATOR_URL
    ):
        import socket
        import threading
        import time

        import uvicorn

        from src.simulator.app import simulator_app

        if check_simulator_health(settings.DEMO_SIMULATOR_URL, timeout=0.5):
            logger.info(
                "Autonomous Keitaro simulator is active and healthy at %s",
                settings.DEMO_SIMULATOR_URL,
            )
        else:
            # Check if port 8001 is open by a non-simulator process
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            port_busy = False
            try:
                s.connect(("127.0.0.1", 8001))
                port_busy = True
            except Exception:
                port_busy = False
            finally:
                s.close()

            if port_busy:
                logger.error(
                    "Port 8001 is occupied by an external process that does not respond to Keitaro simulator health check. Auto-start aborted."
                )
            else:
                logger.info(
                    "Starting autonomous Keitaro simulator on 127.0.0.1:8001 in background thread..."
                )
                sim_config = uvicorn.Config(
                    simulator_app, host="127.0.0.1", port=8001, log_level="warning"
                )
                sim_server = uvicorn.Server(sim_config)

                def _run_sim():
                    try:
                        sim_server.run()
                    except (SystemExit, Exception) as sim_err:
                        logger.warning("Autonomous simulator thread exited: %s", sim_err)

                sim_thread = threading.Thread(target=_run_sim, daemon=True)
                sim_thread.start()

                # Poll health check until simulator is ready (up to 3 seconds)
                ready = False
                for _ in range(30):
                    time.sleep(0.1)
                    if check_simulator_health("http://127.0.0.1:8001", timeout=0.2):
                        ready = True
                        break

                if ready:
                    logger.info(
                        "Autonomous Keitaro simulator is ready and verified healthy at http://127.0.0.1:8001"
                    )
                else:
                    logger.error(
                        "Autonomous Keitaro simulator failed to start or did not become healthy within 3 seconds"
                    )

    # Upgrade only enabled stores; preserve existing drafts and archives.
    migrate_enabled_databases()
    logger.info("Application database migrations completed")

    # Recover interrupted in-progress sync operations from prior crashes
    try:
        recovered = recover_interrupted_streams()
        if recovered > 0:
            logger.info("Recovered %d interrupted streams to unknown status", recovered)
    except Exception as exc:
        logger.warning("Startup sync recovery check encountered: %s", exc)

    yield

    if sim_server:
        sim_server.should_exit = True
        if sim_thread and sim_thread.is_alive():
            sim_thread.join(timeout=2.0)

    logger.info("Shutting down Keitaro Campaign Manager")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Keitaro Campaign Manager",
        description="Campaign Creator & Editor API for Keitaro Admin API",
        version=settings.APP_VERSION,
        lifespan=lifespan,
    )
    app.state.demo_enabled = settings.DEMO_ENABLED

    # Security headers
    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; connect-src 'self'; base-uri 'self'; form-action 'self';"
        )
        return response

    def _sanitize_val_input(val):
        if isinstance(val, dict):
            clean = {}
            for k, v in val.items():
                if any(
                    s in str(k).lower() for s in ("api_key", "password", "token", "secret", "key")
                ):
                    clean[k] = "***"
                else:
                    clean[k] = _sanitize_val_input(v)
            return clean
        if isinstance(val, list):
            return [_sanitize_val_input(x) for x in val]
        return val

    def _sanitize_ctx(ctx):
        if isinstance(ctx, dict):
            res = {}
            for k, v in ctx.items():
                if isinstance(v, BaseException):
                    res[k] = str(v)
                elif isinstance(v, dict):
                    res[k] = _sanitize_ctx(v)
                else:
                    res[k] = v
            return res
        if isinstance(ctx, BaseException):
            return str(ctx)
        return ctx

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        is_auth = request.url.path.startswith("/api/auth/")
        if is_auth and request.url.path == "/api/auth/login":
            from src.core.security import get_client_ip, get_security_store

            client_ip = get_client_ip(request)
            try:
                get_security_store().record_failed_attempt(client_ip)
            except Exception:
                pass

        errors = []
        for err in exc.errors():
            cleaned_err = dict(err)
            loc = cleaned_err.get("loc", ())
            if is_auth or any(k in loc for k in ("api_key", "password", "token", "secret")):
                cleaned_err.pop("input", None)
            elif "input" in cleaned_err:
                cleaned_err["input"] = _sanitize_val_input(cleaned_err["input"])

            if "ctx" in cleaned_err:
                cleaned_err["ctx"] = _sanitize_ctx(cleaned_err["ctx"])
            errors.append(cleaned_err)

        headers = {"Cache-Control": "no-store"} if is_auth else {}
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": errors},
            headers=headers,
        )

    # Strict same-origin or configured allowlist
    cors_origins = getattr(settings, "CORS_ORIGINS", [])
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["*"],
        )

    # Core auth & diagnostics routers
    app.include_router(auth_router)
    app.include_router(health_router)
    app.include_router(context_router)
    # Keep the existing real API aliases. Optional routes do not exist in a
    # normal launch, including in OpenAPI.
    for prefix in ("/api", "/api/real"):
        app.include_router(campaigns_router, prefix=prefix)
        app.include_router(streams_router, prefix=prefix)

    app.include_router(ui_router)
    if settings.DEMO_ENABLED:
        from src.api.routes_auth import mode_router as auth_mode_router
        from src.api.routes_context import mode_router as context_mode_router
        from src.api.routes_demo import router as demo_router
        from src.web.routes_ui import demo_router as demo_ui_router

        app.include_router(auth_mode_router)
        app.include_router(context_mode_router)
        app.include_router(demo_router)
        app.include_router(campaigns_router, prefix="/api/demo")
        app.include_router(streams_router, prefix="/api/demo")
        app.include_router(demo_ui_router)

    # Static assets directory
    static_dir = os.path.join(os.path.dirname(__file__), "web", "static")
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    return app


app = create_app()
