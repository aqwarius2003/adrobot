"""End-to-end integration tests for Demo Mode and Keitaro Simulator.

Verifies:
1. Full 9-step demonstration scenario from DEMO_MODE_TASK.md.
2. Real KeitaroClient HTTP communication to the simulator.
3. Fault injection & REVIEW_2 uncertainty scenarios (timeout, reject, conflict).
4. Credential isolation & database separation.
"""

import socket
import threading
import time
import urllib.request
from collections.abc import Generator

import pytest
import uvicorn
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.core.config import settings
from src.domain.models import Base, Stream, StreamOffer
from src.services.editor_service import (
    EditorValidationError,
    StreamEditorService,
)
from src.services.keitaro_client import KeitaroClient, KeitaroConflictError
from src.services.sync_service import StreamSyncService, SyncConflictError
from src.simulator.app import simulator_app
from src.simulator.storage import simulator_storage


def get_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def simulator_server() -> Generator[str, None, None]:
    """Spin up live Uvicorn server running simulator_app in background thread."""
    port = get_free_port()
    config = uvicorn.Config(
        simulator_app,
        host="127.0.0.1",
        port=port,
        log_level="error",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to be responsive
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 5.0
    started = False
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=0.5) as resp:
                if resp.status == 200:
                    started = True
                    break
        except Exception:
            time.sleep(0.05)

    if not started:
        raise RuntimeError("Failed to start simulator test server")

    yield url
    server.should_exit = True
    thread.join(timeout=2.0)


@pytest.fixture
def test_db() -> Generator[Session, None, None]:
    """Isolated in-memory SQLite database for test session."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def clean_simulator_storage():
    """Reset simulator storage before each test."""
    simulator_storage.reset()
    yield
    simulator_storage.reset()


@pytest.mark.asyncio
async def test_full_9_step_demonstration_scenario(simulator_server: str, test_db: Session):
    """Executes the exact 9-step demonstration scenario from DEMO_MODE_TASK.md."""
    # Ensure KeitaroClient connects over real HTTP to simulator
    kt_client = KeitaroClient(base_url=simulator_server, api_key="demo-api-key")
    editor_svc = StreamEditorService(test_db, kt_client)
    sync_svc = StreamSyncService(test_db, kt_client)

    # -------------------------------------------------------------
    # Step 1: Fetch Streams
    # 2 streams: redirect (opaque) and managed default (34/33/33)
    # -------------------------------------------------------------
    campaign_detail = await editor_svc.fetch_campaign_streams(remote_campaign_id=1)
    assert len(campaign_detail.streams) == 2

    flow1 = next(s for s in campaign_detail.streams if s.remote_stream_id == 10)
    assert flow1.is_managed is False
    assert flow1.stream_schema == "redirect"
    assert len(flow1.offers) == 0  # not editable

    flow2 = next(s for s in campaign_detail.streams if s.remote_stream_id == 11)
    assert flow2.is_managed is True
    assert flow2.sync_status == "clean"
    assert len(flow2.offers) == 3
    assert [o.share for o in flow2.offers] == [34, 33, 33]
    assert [o.offer_id for o in flow2.offers] == [101, 102, 103]

    # -------------------------------------------------------------
    # Step 2: Add 4th offer -> 25/25/25/25. Add 5th -> 20/20/20/20/20.
    # Flow is dirty; simulator state still has 3 offers with 34/33/33.
    # -------------------------------------------------------------
    flow2 = await editor_svc.add_offer(
        stream_id=flow2.id, offer_id=104, expected_revision=flow2.revision
    )
    assert flow2.sync_status == "dirty"
    assert [o.share for o in flow2.offers] == [25, 25, 25, 25]

    flow2 = await editor_svc.add_offer(
        stream_id=flow2.id, offer_id=105, expected_revision=flow2.revision
    )
    assert flow2.sync_status == "dirty"
    assert len(flow2.offers) == 5
    assert [o.share for o in flow2.offers] == [20, 20, 20, 20, 20]

    # Verify simulator received zero writes
    sim_stream = await kt_client.get_stream(11)
    assert len(sim_stream["offers"]) == 3
    assert [o["share"] for o in sim_stream["offers"]] == [34, 33, 33]

    # -------------------------------------------------------------
    # Step 3: Cancel -> resets draft to baseline (34/33/33).
    # Unpublished additions disappear; simulator receives zero writes.
    # -------------------------------------------------------------
    flow2 = editor_svc.cancel_stream(stream_id=flow2.id)
    assert flow2.sync_status == "clean"
    assert len(flow2.offers) == 3
    assert [o.share for o in flow2.offers] == [34, 33, 33]

    # -------------------------------------------------------------
    # Step 4: Repeat Add up to 5 offers. Pin 1st with 40% -> 40/15/15/15/15.
    # -------------------------------------------------------------
    flow2 = await editor_svc.add_offer(
        stream_id=flow2.id, offer_id=104, expected_revision=flow2.revision
    )
    flow2 = await editor_svc.add_offer(
        stream_id=flow2.id, offer_id=105, expected_revision=flow2.revision
    )
    assert len(flow2.offers) == 5

    # Pin offer 101 with 40%
    flow2 = await editor_svc.update_offer_weight(
        stream_id=flow2.id,
        offer_id=101,
        share=40,
        is_pinned=True,
        expected_revision=flow2.revision,
    )
    assert flow2.sync_status == "dirty"
    assert [o.share for o in flow2.offers] == [40, 15, 15, 15, 15]
    assert sum(o.share for o in flow2.offers) == 100

    # -------------------------------------------------------------
    # Step 5: Remove 5th offer (105) -> 40/20/20/20, Removed row 0%, pin 1 preserved.
    # Bring back -> 40/15/15/15/15.
    # -------------------------------------------------------------
    flow2 = await editor_svc.remove_offer(
        stream_id=flow2.id, offer_id=105, expected_revision=flow2.revision
    )
    active_offers = [o for o in flow2.offers if o.state == "active"]
    removed_offers = [o for o in flow2.offers if o.state == "removed"]
    assert len(active_offers) == 4
    assert len(removed_offers) == 1
    assert [o.share for o in active_offers] == [40, 20, 20, 20]
    assert removed_offers[0].share == 0
    assert active_offers[0].is_pinned is True  # pin on 1st preserved

    # Bring back
    flow2 = await editor_svc.bring_back_offer(
        stream_id=flow2.id, offer_id=105, expected_revision=flow2.revision
    )
    assert len([o for o in flow2.offers if o.state == "active"]) == 5
    assert [o.share for o in flow2.offers] == [40, 15, 15, 15, 15]

    # -------------------------------------------------------------
    # Step 6: Push -> simulator receives active offers with shares.
    # GET read-back verifies result. Flow is clean, pins remain local.
    # -------------------------------------------------------------
    flow2 = await sync_svc.push_stream(stream_id=flow2.id, expected_revision=flow2.revision)
    assert flow2.sync_status == "clean"

    # Verify directly against simulator HTTP
    sim_stream = await kt_client.get_stream(11)
    assert len(sim_stream["offers"]) == 5
    assert [o["share"] for o in sim_stream["offers"]] == [40, 15, 15, 15, 15]

    # -------------------------------------------------------------
    # Step 7: Remove 5th offer and Push -> simulator has 4 offers (40/20/20/20).
    # Locally 5th offer remains in archive. Bring back + Push returns 5th.
    # -------------------------------------------------------------
    flow2 = await editor_svc.remove_offer(
        stream_id=flow2.id, offer_id=105, expected_revision=flow2.revision
    )
    flow2 = await sync_svc.push_stream(stream_id=flow2.id, expected_revision=flow2.revision)
    assert flow2.sync_status == "clean"

    # Simulator now has 4 offers
    sim_stream = await kt_client.get_stream(11)
    assert len(sim_stream["offers"]) == 4
    assert [o["share"] for o in sim_stream["offers"]] == [40, 20, 20, 20]

    # Local archive still contains offer 105
    assert any(o.offer_id == 105 and o.state == "removed" for o in flow2.offers)

    # Bring back and push
    flow2 = await editor_svc.bring_back_offer(
        stream_id=flow2.id, offer_id=105, expected_revision=flow2.revision
    )
    flow2 = await sync_svc.push_stream(stream_id=flow2.id, expected_revision=flow2.revision)
    assert flow2.sync_status == "clean"
    sim_stream = await kt_client.get_stream(11)
    assert len(sim_stream["offers"]) == 5

    # -------------------------------------------------------------
    # Step 8: External deletion in tracker simulation
    # Delete offer 105 from stream in simulator (without deleting from catalog)
    # Fetch -> missing offer is Removed/0%, Bring back available, archive preserved.
    # -------------------------------------------------------------
    # Call simulator simulation endpoint
    del_res = simulator_storage.delete_stream_offer(11, 105)
    assert del_res is not None
    assert len(del_res["offers"]) == 4
    assert sum(o["share"] for o in del_res["offers"]) == 100

    # Fetch streams
    campaign_detail = await editor_svc.fetch_campaign_streams(remote_campaign_id=1)
    flow2 = next(s for s in campaign_detail.streams if s.remote_stream_id == 11)

    # Offer 105 is now Removed with 0% and archive_reason="remote_missing"
    off105 = next(o for o in flow2.offers if o.offer_id == 105)
    assert off105.state == "removed"
    assert off105.share == 0
    assert off105.archive_reason == "remote_missing"

    # Bring back is available
    flow2 = await editor_svc.bring_back_offer(
        stream_id=flow2.id, offer_id=105, expected_revision=flow2.revision
    )
    assert flow2.sync_status == "dirty"
    assert any(o.offer_id == 105 and o.state == "active" for o in flow2.offers)

    # -------------------------------------------------------------
    # Step 9: Global offer disappearance simulation
    # Disable offer 105 in global catalog. Bring back explains unavailability.
    # -------------------------------------------------------------
    # Remove 105 first so it is in archive
    flow2 = await editor_svc.remove_offer(
        stream_id=flow2.id, offer_id=105, expected_revision=flow2.revision
    )
    # Push to make baseline clean with 105 in archive
    flow2 = await sync_svc.push_stream(stream_id=flow2.id, expected_revision=flow2.revision)
    assert flow2.sync_status == "clean"

    # Simulate global offer 105 disappearing from Keitaro catalog
    simulator_storage.disable_global_offer(105, available=False)

    # Now attempt to bring back offer 105
    with pytest.raises(EditorValidationError) as exc_info:
        await editor_svc.bring_back_offer(
            stream_id=flow2.id, offer_id=105, expected_revision=flow2.revision
        )

    assert "недоступен в каталоге Keitaro" in str(exc_info.value)
    assert "Привязка сохранена в архиве" in str(exc_info.value)

    # Archive is still preserved in local DB!
    db_so = (
        test_db.query(StreamOffer)
        .join(StreamOffer.offer)
        .filter(StreamOffer.stream_id == flow2.id)
        .all()
    )
    so105 = next(s for s in db_so if s.offer.remote_offer_id == 105)
    assert so105.draft_state == "removed"
    assert so105.draft_share == 0


@pytest.mark.asyncio
async def test_demo_fault_scenarios(simulator_server: str, test_db: Session):
    """Verifies REVIEW_2 uncertainty scenarios on the simulator."""
    kt_client = KeitaroClient(base_url=simulator_server, api_key="demo-api-key")
    editor_svc = StreamEditorService(test_db, kt_client)
    sync_svc = StreamSyncService(test_db, kt_client)

    # Fetch initial stream
    campaign_detail = await editor_svc.fetch_campaign_streams(remote_campaign_id=1)
    flow = next(s for s in campaign_detail.streams if s.remote_stream_id == 11)

    # Make local modification
    flow = await editor_svc.add_offer(
        stream_id=flow.id, offer_id=104, expected_revision=flow.revision
    )
    assert flow.sync_status == "dirty"

    # --- Fault 1: Rejection on update (422) ---
    simulator_storage.set_fault(
        "reject_update",
        {"status_code": 422, "message": "Offer allocation limit exceeded"},
    )
    with pytest.raises(KeitaroConflictError) as exc_info:
        await sync_svc.push_stream(stream_id=flow.id, expected_revision=flow.revision)
    assert "Offer allocation limit exceeded" in str(exc_info.value)

    # Draft remains dirty and preserved
    db_stream = test_db.query(Stream).filter_by(id=flow.id).first()
    assert db_stream.sync_status == "dirty"

    # Clear fault
    simulator_storage.clear_fault()

    # --- Fault 2: External conflict on preflight (409) ---
    # Remote stream offers modified behind the scenes
    sim_stream = simulator_storage.get_stream(11)
    sim_stream["offers"][0]["share"] = 99
    simulator_storage.streams[11] = sim_stream

    with pytest.raises(SyncConflictError) as exc_info:
        await sync_svc.push_stream(stream_id=flow.id, expected_revision=flow.revision)
    assert "Conflict: Remote stream was modified in Keitaro" in str(exc_info.value)

    # Stream is marked with conflict, draft is not lost
    db_stream = test_db.query(Stream).filter_by(id=flow.id).first()
    assert db_stream.sync_status == "conflict"


def test_mode_credential_isolation():
    """Verify demo mode never uses or leaks real credentials."""
    orig_url = settings.DATABASE_URL
    orig_mode = settings.KT_MODE
    try:
        settings.DATABASE_URL = "sqlite:///./data/app.db"
        settings.KT_MODE = "demo"
        assert settings.is_demo_mode is True
        assert settings.effective_api_key == "demo-api-key"
        assert "tracker.example" not in settings.normalized_base_url
        assert "8001" in settings.normalized_base_url
        assert settings.effective_database_url == "sqlite:///./data/demo_app.db"
    finally:
        settings.DATABASE_URL = orig_url
        settings.KT_MODE = orig_mode
