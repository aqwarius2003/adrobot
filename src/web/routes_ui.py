import os

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from src.core.config import settings
from src.core.security import (
    generate_csrf_token,
    get_current_session,
    get_security_store,
    validate_safe_return_url,
)

router = APIRouter(include_in_schema=False)
demo_router = APIRouter(include_in_schema=False)


def _template_context(request: Request) -> dict:
    return {"demo_enabled": request.app.state.demo_enabled}


templates_dir = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=templates_dir, context_processors=[_template_context])


@router.get("/login")
def login_page(request: Request):
    safe_return = validate_safe_return_url(request.query_params.get("return_to"))
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"request": request, "return_to": safe_return},
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


def _get_effective_mode(request: Request) -> str:
    cookie_mode = request.cookies.get("adrobot_mode")
    if cookie_mode == "real" or (cookie_mode == "demo" and settings.DEMO_ENABLED):
        return cookie_mode
    return settings.default_mode


@router.get("/")
def index(request: Request):
    """Initial root visit:
    If demo cookie -> /demo/editor
    If active real session -> /real/editor
    Otherwise -> /login
    """
    cookie_mode = request.cookies.get("adrobot_mode")
    if settings.DEMO_ENABLED and (
        cookie_mode == "demo" or (not cookie_mode and settings.is_demo_mode)
    ):
        return RedirectResponse(url="/demo/editor", status_code=307)

    session = get_current_session(request, store=get_security_store())
    if session:
        return RedirectResponse(url="/real/editor", status_code=307)

    return RedirectResponse(url="/login", status_code=307)


@router.get("/editor")
def legacy_editor_redirect(request: Request):
    """Redirect unadorned /editor preserving query parameters and checking auth."""
    session = get_current_session(request, store=get_security_store())
    cookie_mode = request.cookies.get("adrobot_mode")

    if settings.DEMO_ENABLED and (
        cookie_mode == "demo" or (not cookie_mode and settings.is_demo_mode)
    ):
        mode = "demo"
    elif session:
        mode = "real"
    else:
        # Default to real, which will redirect to /login
        qs = str(request.query_params)
        return_to = f"/real/editor?{qs}" if qs else "/real/editor"
        return RedirectResponse(url=f"/login?return_to={return_to}", status_code=307)

    qs = str(request.query_params)
    target = f"/{mode}/editor"
    if qs:
        target = f"{target}?{qs}"
    return RedirectResponse(url=target, status_code=307)


@router.get("/real/editor")
def real_editor_page(request: Request):
    session = get_current_session(request, store=get_security_store())
    if not session:
        return RedirectResponse(url="/login?return_to=/real/editor", status_code=307)

    csrf = generate_csrf_token(session["token_hash"])
    return templates.TemplateResponse(
        request=request,
        name="editor.html",
        context={
            "active_page": "editor",
            "current_mode": "real",
            "is_demo": False,
            "badge_text": "Подключение: Keitaro",
            "push_btn_text": "Push to KT",
            "has_simulator_tab": False,
            "api_prefix": "/api/real",
            "surname": settings.CAMPAIGN_SURNAME,
            "version": settings.APP_VERSION,
            "keitaro_configured": settings.is_keitaro_configured,
            "keitaro_base_url": settings.get_real_keitaro_base_url(),
            "csrf_token": csrf,
        },
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


@demo_router.get("/demo/editor")
def demo_editor_page(request: Request):
    session = get_current_session(request, store=get_security_store())
    if session:
        # User has an active real session and navigated directly to demo URL.
        # Direct switch requires confirmation via POST to avoid CSRF logout on GET.
        return templates.TemplateResponse(
            request=request,
            name="confirm_switch_demo.html",
            context={
                "request": request,
                "target_url": "/demo/editor",
                "csrf_token": generate_csrf_token(session["token_hash"]),
            },
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    return templates.TemplateResponse(
        request=request,
        name="editor.html",
        context={
            "active_page": "editor",
            "current_mode": "demo",
            "is_demo": True,
            "badge_text": "Демо · симулятор",
            "push_btn_text": "Push to Demo",
            "has_simulator_tab": True,
            "api_prefix": "/api/demo",
            "surname": settings.CAMPAIGN_SURNAME,
            "version": settings.APP_VERSION,
            "keitaro_configured": True,
            "keitaro_base_url": settings.get_demo_keitaro_base_url(),
        },
    )


@demo_router.get("/demo/simulator")
def demo_simulator_page(request: Request):
    session = get_current_session(request, store=get_security_store())
    if session:
        return templates.TemplateResponse(
            request=request,
            name="confirm_switch_demo.html",
            context={
                "request": request,
                "target_url": "/demo/simulator",
                "csrf_token": generate_csrf_token(session["token_hash"]),
            },
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    return templates.TemplateResponse(
        request=request,
        name="simulator.html",
        context={
            "active_page": "simulator",
            "current_mode": "demo",
            "is_demo": True,
            "badge_text": "Демо · симулятор",
            "has_simulator_tab": True,
            "api_prefix": "/api/demo",
            "surname": settings.CAMPAIGN_SURNAME,
            "version": settings.APP_VERSION,
            "keitaro_configured": True,
            "keitaro_base_url": settings.get_demo_keitaro_base_url(),
        },
    )


@demo_router.get("/real/simulator")
def real_simulator_forbidden():
    """Simulator tab is strictly demo-only."""
    return RedirectResponse(url="/real/editor")


@router.get("/creator")
def legacy_creator_redirect(request: Request):
    mode = _get_effective_mode(request)
    return RedirectResponse(url=f"/{mode}/creator")


@router.get("/real/creator")
def real_creator_page(request: Request):
    session = get_current_session(request, store=get_security_store())
    if not session:
        return RedirectResponse(url="/login?return_to=/real/creator", status_code=307)

    return templates.TemplateResponse(
        request=request,
        name="creator.html",
        context={
            "active_page": "creator",
            "current_mode": "real",
            "is_demo": False,
            "has_simulator_tab": False,
            "api_prefix": "/api/real",
            "surname": settings.CAMPAIGN_SURNAME,
            "version": settings.APP_VERSION,
            "keitaro_configured": settings.is_real_mode_ready()[0],
            "keitaro_base_url": settings.get_real_keitaro_base_url(),
            "csrf_token": generate_csrf_token(session["token_hash"]),
        },
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


@demo_router.get("/demo/creator")
def demo_creator_page(request: Request):
    """Create campaigns against the isolated simulator connection."""
    session = get_current_session(request, store=get_security_store())
    if session:
        return templates.TemplateResponse(
            request=request,
            name="confirm_switch_demo.html",
            context={
                "request": request,
                "target_url": "/demo/creator",
                "csrf_token": generate_csrf_token(session["token_hash"]),
            },
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    return templates.TemplateResponse(
        request=request,
        name="creator.html",
        context={
            "active_page": "creator",
            "current_mode": "demo",
            "is_demo": True,
            "badge_text": "Демо · симулятор",
            "has_simulator_tab": True,
            "api_prefix": "/api/demo",
            "surname": settings.CAMPAIGN_SURNAME,
            "version": settings.APP_VERSION,
            "keitaro_configured": True,
            "keitaro_base_url": settings.get_demo_keitaro_base_url(),
        },
    )
