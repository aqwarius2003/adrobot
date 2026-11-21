"""Acceptance checks use fresh processes, without pytest's opt-in simulator fixtures."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(
    scope="module",
    params=[
        "default",
        "disabled_with_legacy_config",
        "disabled_with_invalid_mode",
        "disabled_with_empty_mode",
    ],
)
def real_launch(tmp_path_factory, request):
    directory = tmp_path_factory.mktemp("keitaro_only")
    repository = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.update(
        PYTHONPATH=str(repository),
        APP_ENV="development",
        KT_MODE="real",
        DEMO_SIMULATOR_URL="http://127.0.0.1:8001",
        DATABASE_URL=f"sqlite:///{directory / 'app.db'}",
        REAL_DATABASE_URL=f"sqlite:///{directory / 'app.db'}",
        SECURITY_DATABASE_URL=f"sqlite:///{directory / 'security.db'}",
        DEMO_DATABASE_URL=f"sqlite:///{directory / 'forbidden' / 'demo.db'}",
        SIMULATOR_STATE_FILE=str(directory / "forbidden" / "simulator.json"),
        KEITARO_BASE_URL="https://tracker.example",
        REAL_KEITARO_BASE_URL="https://tracker.example",
        KEITARO_API_KEY="",
        REAL_KEITARO_API_KEY="",
    )
    env.pop("DEMO_ENABLED", None)
    if request.param == "disabled_with_legacy_config":
        env.update(
            DEMO_ENABLED="false",
            KT_MODE="demo",
            DEMO_DATABASE_URL="missing_driver:///must-not-be-loaded",
            DEMO_KEITARO_BASE_URL="https://tracker.example",
        )
    if request.param in ("disabled_with_invalid_mode", "disabled_with_empty_mode"):
        env.update(
            DEMO_ENABLED="false",
            KT_MODE="outdated-value" if request.param == "disabled_with_invalid_mode" else "",
        )
    result = subprocess.run(
        [sys.executable, "-c", _LAUNCH_PROBE],
        cwd=directory,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout), directory


_LAUNCH_PROBE = r"""
import contextlib
import io
import json
import sqlite3
import sys
import traceback

connections = []
def audit(event, args):
    if event == "socket.connect" and isinstance(args[1], tuple):
        # Windows asyncio creates an internal TCP socketpair, even for an empty
        # TestClient. It is not an application/upstream network request.
        if any(frame.name == "socketpair" for frame in traceback.extract_stack()):
            return
        connections.append(str(args[1]))
sys.addaudithook(audit)

from fastapi.testclient import TestClient
from src.core.config import settings
from src.core import database
from src.core.security import get_security_store
from src.main import create_app

output = io.StringIO()
report = {}
with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
    app = create_app()
    with TestClient(app) as client:
        report["startup"] = output.getvalue()
        report["health"] = client.get("/api/health").json()
        report["schema"] = client.get("/openapi.json").json()
        report["login"] = client.get("/login").text
        report["anonymous_session"] = client.get("/api/auth/session").json()
        report["auth_error"] = client.post("/api/auth/login", json={"api_key": "synthetic"}).json()
        client.cookies.set("adrobot_mode", "demo")
        report["root"] = client.get("/", follow_redirects=False).headers.get("location")
        report["legacy_creator"] = client.get("/creator", follow_redirects=False).headers.get("location")
        report["context"] = client.get("/api/session/context").json()
        token = get_security_store().create_session(key="synthetic-launch-fixture")
        client.cookies.set("adrobot_session", token)
        report["editor"] = client.get("/real/editor").text
        report["creator"] = client.get("/real/creator").text
        report["blocked"] = {}
        for path in ("/demo/editor", "/demo/creator", "/demo/simulator",
                     "/api/demo/campaigns", "/api/demo/simulator/state", "/real/simulator"):
            report["blocked"][path] = client.get(path, follow_redirects=False).status_code
        for path in ("/api/auth/switch-mode", "/api/session/context"):
            report["blocked"][path] = client.post(path, json={"mode": "demo"}).status_code
        report["still_authenticated"] = client.get("/api/auth/session").json()["authenticated"]

report["modules"] = [name for name in sys.modules if name.startswith("src.simulator") or name == "src.api.routes_demo"]
report["demo_engine_created"] = database.demo_engine is not None
report["connections"] = connections
with sqlite3.connect("app.db") as connection:
    report["tables"] = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    report["campaign_count"] = connection.execute("SELECT count(*) FROM campaigns").fetchone()[0]
    report["migration"] = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] if "alembic_version" in report["tables"] else None
print(json.dumps(report, ensure_ascii=True))
"""


def test_clean_start_never_loads_or_contacts_simulator(real_launch):
    report, directory = real_launch
    assert report["modules"] == []
    assert not report["demo_engine_created"]
    assert report["connections"] == []
    assert not (directory / "forbidden").exists()
    assert "demo" not in report["startup"].lower()
    assert "simulator" not in report["startup"].lower()


def test_only_working_databases_are_initialized_and_migrated(real_launch):
    report, directory = real_launch
    assert (directory / "app.db").exists()
    assert (directory / "security.db").exists()
    assert report["migration"] == "b7c8d9e0f1a2"
    assert report["campaign_count"] == 0
    assert all("demo" not in name.lower() for name in report["tables"])
    assert report["health"]["status"] == "ok"
    assert report["health"]["kt_mode"] == "real"


def test_disabled_routes_are_absent_and_cannot_revoke_session(real_launch):
    report, _ = real_launch
    assert all(code in (404, 405) for code in report["blocked"].values()), report["blocked"]
    assert report["still_authenticated"]
    assert "demo" not in json.dumps(report["schema"]).lower()
    assert "/api/auth/switch-mode" not in report["schema"]["paths"]


def test_old_mode_cookie_cannot_select_disabled_connection(real_launch):
    report, _ = real_launch
    assert report["root"] == "/login"
    assert report["legacy_creator"] == "/real/creator"
    assert report["context"]["mode"] == "real"
    assert report["anonymous_session"]["mode"] == "real"


@pytest.mark.parametrize("page", ["login", "creator", "editor"])
def test_working_pages_have_no_demo_markup_or_switch_scripts(real_launch, page):
    report, _ = real_launch
    html = report[page].lower()
    for forbidden in ("demo", "демо", "симулятор", "connection_mode.js", "modeswitch"):
        assert forbidden not in html
    assert "демо" not in report["auth_error"]["detail"].lower()


def test_automatic_migration_preserves_legacy_draft_and_ignores_existing_demo(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.update(
        PYTHONPATH=str(repository),
        APP_ENV="development",
        DEMO_ENABLED="false",
        KT_MODE="real",
        DATABASE_URL=f"sqlite:///{tmp_path / 'app.db'}",
        REAL_DATABASE_URL=f"sqlite:///{tmp_path / 'app.db'}",
        SECURITY_DATABASE_URL=f"sqlite:///{tmp_path / 'security.db'}",
        DEMO_DATABASE_URL=f"sqlite:///{tmp_path / 'existing-demo.db'}",
        SIMULATOR_STATE_FILE=str(tmp_path / "existing-simulator.json"),
    )
    result = subprocess.run(
        [sys.executable, "-c", _PRESERVATION_PROBE],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_startup_migrates_the_bound_in_memory_databases(monkeypatch):
    from sqlalchemy import inspect

    from src.core import database
    from src.core.config import settings

    original = (
        database.real_engine,
        database.RealSessionLocal,
        database.demo_engine,
        database.DemoSessionLocal,
        database.engine,
        database.SessionLocal,
    )
    monkeypatch.setattr(settings, "DEMO_ENABLED", True)
    try:
        database.configure_engines(real_url="sqlite:///:memory:", demo_url="sqlite:///:memory:")
        database.migrate_enabled_databases()
        for engine in (database.real_engine, database.demo_engine):
            assert {"campaigns", "streams", "stream_offers", "alembic_version"} <= set(
                inspect(engine).get_table_names()
            )
        assert database.real_engine is not database.demo_engine
    finally:
        database.real_engine.dispose()
        database.demo_engine.dispose()
        (
            database.real_engine,
            database.RealSessionLocal,
            database.demo_engine,
            database.DemoSessionLocal,
            database.engine,
            database.SessionLocal,
        ) = original


_PRESERVATION_PROBE = r"""
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import text
from src.core.database import Base, real_engine, RealSessionLocal
from src.domain.models import Campaign, Offer, Stream, StreamOffer
from src.main import create_app

# A pre-Alembic runtime schema with a locally changed, pinned, archived draft.
Base.metadata.create_all(real_engine)
with RealSessionLocal() as session:
    campaign = Campaign(remote_campaign_id=777, name="Existing", alias="existing")
    stream = Stream(campaign=campaign, remote_stream_id=888, name="Flow 2", sync_status="dirty")
    session.add_all([campaign, stream])
    for remote_id, baseline, draft, state, pinned in (
        (101, 50, 25, "active", True),
        (102, 50, 75, "active", False),
        (103, 0, 0, "removed", False),
    ):
        offer = Offer(remote_offer_id=remote_id, name=f"Offer {remote_id}")
        session.add(StreamOffer(stream=stream, offer=offer, baseline_share=baseline,
            draft_share=draft, draft_state=state, draft_pinned=pinned))
    session.commit()

optional = [Path("existing-demo.db"), Path("existing-simulator.json")]
for path in optional:
    path.write_bytes(b"deliberately invalid optional store; must stay untouched")
before = {path: path.read_bytes() for path in optional}

for _ in range(2):
    with TestClient(create_app()) as client:
        assert client.get("/api/health").json()["status"] == "ok"
    with RealSessionLocal() as session:
        stream = session.query(Stream).one()
        assert stream.sync_status == "dirty"
        offers = sorted(stream.stream_offers, key=lambda item: item.offer.remote_offer_id)
        assert [(o.baseline_share, o.draft_share, o.draft_state, o.draft_pinned) for o in offers] == [
            (50, 25, "active", True), (50, 75, "active", False), (0, 0, "removed", False)]
        assert session.execute(text("SELECT version_num FROM alembic_version")).scalar() == "b7c8d9e0f1a2"
    assert all(path.read_bytes() == original for path, original in before.items())
"""
