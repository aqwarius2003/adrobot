import os

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from src.core.config import settings

router = APIRouter(include_in_schema=False)

templates_dir = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=templates_dir)


@router.get("/")
def index():
    if not settings.is_demo_mode:
        return RedirectResponse(url="/editor")
    return RedirectResponse(url="/creator")


@router.get("/creator")
def creator_page(request: Request):
    if not settings.is_demo_mode:
        return RedirectResponse(url="/editor?notice=creator_unavailable_in_real_mode")
    return templates.TemplateResponse(
        request=request,
        name="creator.html",
        context={
            "active_page": "creator",
            "surname": settings.CAMPAIGN_SURNAME,
            "version": settings.APP_VERSION,
            "keitaro_configured": settings.is_keitaro_configured,
            "kt_mode": settings.KT_MODE,
            "is_demo": settings.is_demo_mode,
            "demo_app_url": settings.DEMO_APP_URL,
            "real_app_url": settings.REAL_APP_URL,
            "keitaro_base_url": settings.KEITARO_BASE_URL,
        },
    )


@router.get("/editor")
def editor_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="editor.html",
        context={
            "active_page": "editor",
            "surname": settings.CAMPAIGN_SURNAME,
            "version": settings.APP_VERSION,
            "keitaro_configured": settings.is_keitaro_configured,
            "kt_mode": settings.KT_MODE,
            "is_demo": settings.is_demo_mode,
            "demo_app_url": settings.DEMO_APP_URL,
            "real_app_url": settings.REAL_APP_URL,
            "keitaro_base_url": settings.KEITARO_BASE_URL,
        },
    )
