import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.api.routes_health import router as health_router
from src.core.config import settings
from src.core.database import Base, engine
from src.core.logging import logger, setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize logger
    setup_logging()
    logger.info("Starting Keitaro Campaign Manager (version=%s, env=%s)", settings.APP_VERSION, settings.APP_ENV)

    # Auto-create tables for development / initial skeleton
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized successfully at %s", settings.DATABASE_URL)

    yield

    logger.info("Shutting down Keitaro Campaign Manager")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Keitaro Campaign Manager",
        description="Campaign Creator & Editor API for Keitaro Admin API",
        version=settings.APP_VERSION,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Core health & diagnostics router
    app.include_router(health_router)

    # Static assets directory
    static_dir = os.path.join(os.path.dirname(__file__), "web", "static")
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    return app


app = create_app()
