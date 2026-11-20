import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.core.config import (
    validate_database_isolation,
    validate_upstream_isolation,
)
from src.core.connection_context import (
    build_connection_context,
)
from src.core.database import Base
from src.domain.models import Campaign


def test_invalid_mode_raises_value_error():
    with pytest.raises(ValueError, match="Invalid connection mode"):
        build_connection_context("unknown_mode")


def test_matching_database_urls_rejected():
    # 1. Identical strings
    with pytest.raises(ValueError, match="cannot be identical"):
        validate_database_isolation("sqlite:///./data/app.db", "sqlite:///./data/app.db")

    # 2. Same normalized physical SQLite file with different path notations
    cwd = os.getcwd()
    path1 = f"sqlite:///{os.path.join(cwd, 'data', 'app.db')}"
    path2 = "sqlite:///./data/app.db"
    with pytest.raises(ValueError, match="point to the same physical SQLite file"):
        validate_database_isolation(path1, path2)


def test_demo_pointing_to_real_keitaro_rejected():
    with pytest.raises(ValueError, match="cannot point to the live REAL Keitaro tracker"):
        validate_upstream_isolation(
            "https://mytracker.keitaro.io/admin",
            "https://mytracker.keitaro.io/admin_api/v1",
        )


def test_demo_mode_never_sends_real_key_or_url(monkeypatch):
    monkeypatch.setattr(
        "src.core.config.settings.REAL_KEITARO_BASE_URL", "https://real-tracker.com"
    )
    monkeypatch.setattr(
        "src.core.config.settings.REAL_KEITARO_API_KEY", "real-super-secret-key-123"
    )
    monkeypatch.setattr("src.core.config.settings.DEMO_SIMULATOR_URL", "http://127.0.0.1:8001")
    monkeypatch.setattr("src.core.config.settings.DEMO_KEITARO_API_KEY", "demo-api-key")

    real_ctx = build_connection_context("real")
    assert real_ctx.base_url == "https://real-tracker.com"
    assert real_ctx.api_key == "real-super-secret-key-123"
    assert real_ctx.is_demo is False

    demo_ctx = build_connection_context("demo")
    assert demo_ctx.base_url == "http://127.0.0.1:8001"
    assert demo_ctx.api_key == "demo-api-key"
    assert demo_ctx.is_demo is True
    # Real key must NEVER be in demo context
    assert "real-super-secret" not in demo_ctx.api_key


def test_database_isolation_with_identical_ids():
    """Verify that real and demo databases maintain separate tables, IDs, and states.

    The same Campaign local ID (1) and remote ID (10) can exist in both databases
    with different names without collision or state leak.
    """
    engine_real = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    engine_demo = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    Base.metadata.create_all(bind=engine_real)
    Base.metadata.create_all(bind=engine_demo)

    SessionReal = sessionmaker(bind=engine_real)
    SessionDemo = sessionmaker(bind=engine_demo)

    # Insert campaign in real DB
    with SessionReal() as s_real:
        c_real = Campaign(
            id=1,
            remote_campaign_id=10,
            name="Real Campaign #10",
            alias="real-10",
        )
        s_real.add(c_real)
        s_real.commit()

    # Insert campaign in demo DB with identical local ID and remote ID
    with SessionDemo() as s_demo:
        c_demo = Campaign(
            id=1,
            remote_campaign_id=10,
            name="Demo Synthetic Campaign #10",
            alias="demo-10",
        )
        s_demo.add(c_demo)
        s_demo.commit()

    # Verify both databases preserve their own independent state
    with SessionReal() as s_real:
        c = s_real.query(Campaign).filter_by(id=1).first()
        assert c is not None
        assert c.name == "Real Campaign #10"

    with SessionDemo() as s_demo:
        c = s_demo.query(Campaign).filter_by(id=1).first()
        assert c is not None
        assert c.name == "Demo Synthetic Campaign #10"
