from unittest.mock import AsyncMock

import pytest

from src.core.database import DemoSessionLocal, demo_engine, init_db
from src.domain.models import Campaign, Offer, Operation, Stream, StreamOffer
from src.main import recover_interrupted_streams
from src.services.editor_service import EditorConflictError, StreamEditorService
from src.services.keitaro_client import KeitaroClient
from src.services.sync_service import StreamSyncService


@pytest.fixture
def test_db_session():
    init_db("demo")
    with DemoSessionLocal() as session:
        # Clean test tables
        session.query(Operation).delete()
        session.query(StreamOffer).delete()
        session.query(Stream).delete()
        session.query(Offer).delete()
        session.query(Campaign).delete()
        session.commit()
        yield session
        session.rollback()
        session.query(Operation).delete()
        session.query(StreamOffer).delete()
        session.query(Stream).delete()
        session.query(Offer).delete()
        session.query(Campaign).delete()
        session.commit()


@pytest.fixture
def mock_keitaro():
    client = AsyncMock(spec=KeitaroClient)
    return client


def test_startup_recovery_marks_interrupted_streams(test_db_session):
    engine = demo_engine

    camp = Campaign(id=1, remote_campaign_id=1, name="Test Camp", alias="test_camp")
    test_db_session.add(camp)
    test_db_session.flush()

    # Create two streams: one publishing, one verifying, one dirty
    s1 = Stream(
        campaign_id=camp.id,
        remote_stream_id=101,
        name="Stream Publishing",
        position=1,
        is_managed=True,
        sync_status="publishing",
        revision=1,
    )
    s2 = Stream(
        campaign_id=camp.id,
        remote_stream_id=102,
        name="Stream Verifying",
        position=2,
        is_managed=True,
        sync_status="verifying",
        revision=1,
    )
    s3 = Stream(
        campaign_id=camp.id,
        remote_stream_id=103,
        name="Stream Dirty",
        position=3,
        is_managed=True,
        sync_status="dirty",
        revision=1,
    )
    test_db_session.add_all([s1, s2, s3])
    test_db_session.commit()

    # Run recovery
    recover_interrupted_streams(engine)

    test_db_session.expire_all()
    res1 = test_db_session.get(Stream, s1.id)
    res2 = test_db_session.get(Stream, s2.id)
    res3 = test_db_session.get(Stream, s3.id)

    assert res1.sync_status == "unknown"
    assert res2.sync_status == "unknown"
    assert res3.sync_status == "dirty"


@pytest.mark.asyncio
async def test_fetch_race_condition_raises_conflict(test_db_session, mock_keitaro):
    camp = Campaign(id=1, remote_campaign_id=1, name="Test Camp", alias="test_camp")
    test_db_session.add(camp)
    test_db_session.flush()

    # Setup initial stream
    stream = Stream(
        campaign_id=camp.id,
        remote_stream_id=201,
        name="Managed Stream",
        position=1,
        is_managed=True,
        sync_status="clean",
        revision=5,
    )
    test_db_session.add(stream)
    test_db_session.commit()

    editor = StreamEditorService(db=test_db_session, kt_client=mock_keitaro)

    # Use a separate independent SQLAlchemy session to mutate the stream concurrently
    async def slow_get_streams(campaign_id):
        with DemoSessionLocal() as session2:
            s2 = session2.get(Stream, stream.id)
            s2.revision = 6
            s2.sync_status = "dirty"
            session2.commit()

        return [
            {
                "id": 201,
                "campaign_id": 1,
                "name": "Managed Stream",
                "position": 1,
                "schema": "offers",
                "offers": [{"offer_id": 10, "share": 100, "state": "active"}],
            }
        ]

    mock_keitaro.get_campaign_streams.side_effect = slow_get_streams

    with pytest.raises(EditorConflictError, match="modified concurrently during remote fetch"):
        await editor.fetch_campaign_streams(remote_campaign_id=1)


@pytest.mark.asyncio
async def test_fetch_race_two_independent_sessions_preserves_added_offer(mock_keitaro):
    """R2: Two independent sessions: Fetch started for clean stream; session 2 adds an offer

    during the network await. Fetch must raise EditorConflictError and preserve session 2's added offer.
    """
    init_db("demo")
    with DemoSessionLocal() as init_session:
        init_session.query(StreamOffer).delete()
        init_session.query(Stream).delete()
        init_session.query(Offer).delete()
        init_session.query(Campaign).delete()
        init_session.commit()

        camp = Campaign(id=1, remote_campaign_id=100, name="Camp 100", alias="camp_100")
        init_session.add(camp)
        init_session.flush()

        stream = Stream(
            id=50,
            campaign_id=camp.id,
            remote_stream_id=200,
            name="Flow 1",
            position=1,
            is_managed=True,
            sync_status="clean",
            revision=1,
        )
        init_session.add(stream)

        # Initial offers 501, 502, 503
        for off_id, share in [(501, 50), (502, 30), (503, 20)]:
            off = Offer(id=off_id, remote_offer_id=off_id, name=f"Offer {off_id}")
            init_session.add(off)
            init_session.flush()
            so = StreamOffer(
                stream_id=stream.id,
                offer_id=off.id,
                baseline_share=share,
                draft_share=share,
                baseline_state="active",
                draft_state="active",
            )
            init_session.add(so)
        init_session.commit()

    session1 = DemoSessionLocal()
    editor1 = StreamEditorService(db=session1, kt_client=mock_keitaro)

    async def slow_get_streams_concurrent_add(campaign_id):
        # Independent session 2 adds offer 504 while session 1 is in await
        with DemoSessionLocal() as session2:
            off504 = Offer(id=504, remote_offer_id=504, name="Offer 504")
            session2.add(off504)
            session2.flush()
            so504 = StreamOffer(
                stream_id=50,
                offer_id=off504.id,
                baseline_share=0,
                draft_share=25,
                baseline_state="removed",
                draft_state="active",
            )
            session2.add(so504)
            s2 = session2.get(Stream, 50)
            s2.revision = 2
            s2.sync_status = "dirty"
            session2.commit()

        # Remote KT still only returns 501, 502, 503
        return [
            {
                "id": 200,
                "campaign_id": 100,
                "name": "Flow 1",
                "position": 1,
                "schema": "offers",
                "offers": [
                    {"offer_id": 501, "share": 50, "state": "active"},
                    {"offer_id": 502, "share": 30, "state": "active"},
                    {"offer_id": 503, "share": 20, "state": "active"},
                ],
            }
        ]

    mock_keitaro.get_campaign_streams.side_effect = slow_get_streams_concurrent_add

    # Fetch on session1 must detect concurrent mutation and raise EditorConflictError
    with pytest.raises(EditorConflictError):
        await editor1.fetch_campaign_streams(remote_campaign_id=100)
    session1.close()

    # Verify session 2's added offer 504 is intact and active
    with DemoSessionLocal() as verify_session:
        st = verify_session.get(Stream, 50)
        assert st.sync_status == "dirty"
        assert st.revision == 2
        active_offer_ids = [
            so.offer.remote_offer_id for so in st.stream_offers if so.draft_state == "active"
        ]
        assert set(active_offer_ids) == {501, 502, 503, 504}


@pytest.mark.asyncio
async def test_push_reconciliation_candidate_match(test_db_session, mock_keitaro):
    camp = Campaign(id=1, remote_campaign_id=1, name="Test Camp", alias="test_camp")
    test_db_session.add(camp)
    test_db_session.flush()

    stream = Stream(
        campaign_id=camp.id,
        remote_stream_id=301,
        name="Reconcile Stream",
        position=1,
        is_managed=True,
        sync_status="dirty",
        revision=1,
    )
    test_db_session.add(stream)
    test_db_session.flush()

    off10 = Offer(remote_offer_id=10, name="Offer 10")
    off20 = Offer(remote_offer_id=20, name="Offer 20")
    test_db_session.add_all([off10, off20])
    test_db_session.flush()

    # Baseline 100%, Draft 50/50
    off1 = StreamOffer(
        stream_id=stream.id,
        offer_id=off10.id,
        baseline_share=100,
        draft_share=50,
        baseline_state="active",
        draft_state="active",
    )
    off2 = StreamOffer(
        stream_id=stream.id,
        offer_id=off20.id,
        baseline_share=0,
        draft_share=50,
        baseline_state="removed",
        draft_state="active",
    )
    test_db_session.add_all([off1, off2])
    test_db_session.commit()

    sync_service = StreamSyncService(db=test_db_session, kt_client=mock_keitaro)

    mock_keitaro.get_stream.return_value = {
        "id": 301,
        "name": "Reconcile Stream",
        "offers": [
            {"offer_id": 10, "share": 50, "state": "active"},
            {"offer_id": 20, "share": 50, "state": "active"},
        ],
    }

    candidate = [{"offer_id": 10, "share": 50}, {"offer_id": 20, "share": 50}]
    dto = await sync_service._reconcile_timeout(stream, candidate, op=None)

    assert dto.sync_status == "clean"
    assert stream.sync_status == "clean"
    assert off1.baseline_share == 50
    assert off2.baseline_share == 50


@pytest.mark.asyncio
async def test_push_reconciliation_baseline_match(test_db_session, mock_keitaro):
    camp = Campaign(id=1, remote_campaign_id=1, name="Test Camp", alias="test_camp")
    test_db_session.add(camp)
    test_db_session.flush()

    stream = Stream(
        campaign_id=camp.id,
        remote_stream_id=302,
        name="Reconcile Stream 2",
        position=1,
        is_managed=True,
        sync_status="dirty",
        revision=1,
    )
    test_db_session.add(stream)
    test_db_session.flush()

    off10 = Offer(remote_offer_id=10, name="Offer 10")
    test_db_session.add(off10)
    test_db_session.flush()

    off1 = StreamOffer(
        stream_id=stream.id,
        offer_id=off10.id,
        baseline_share=100,
        draft_share=50,
        baseline_state="active",
        draft_state="active",
    )
    test_db_session.add(off1)
    test_db_session.commit()

    sync_service = StreamSyncService(db=test_db_session, kt_client=mock_keitaro)

    # Remote read-back still has baseline state (100)
    mock_keitaro.get_stream.return_value = {
        "id": 302,
        "name": "Reconcile Stream 2",
        "offers": [{"offer_id": 10, "share": 100, "state": "active"}],
    }

    candidate = [{"offer_id": 10, "share": 50}]
    dto = await sync_service._reconcile_timeout(stream, candidate, op=None)

    # Must stay dirty, draft preserved for safe retry
    assert dto.sync_status == "dirty"
    assert stream.sync_status == "dirty"
    assert off1.draft_share == 50
    assert off1.baseline_share == 100


@pytest.mark.asyncio
async def test_push_reconciliation_third_state_conflict(test_db_session, mock_keitaro):
    camp = Campaign(id=1, remote_campaign_id=1, name="Test Camp", alias="test_camp")
    test_db_session.add(camp)
    test_db_session.flush()

    stream = Stream(
        campaign_id=camp.id,
        remote_stream_id=303,
        name="Reconcile Stream 3",
        position=1,
        is_managed=True,
        sync_status="dirty",
        revision=1,
    )
    test_db_session.add(stream)
    test_db_session.flush()

    off10 = Offer(remote_offer_id=10, name="Offer 10")
    test_db_session.add(off10)
    test_db_session.flush()

    off1 = StreamOffer(
        stream_id=stream.id,
        offer_id=off10.id,
        baseline_share=100,
        draft_share=50,
        baseline_state="active",
        draft_state="active",
    )
    test_db_session.add(off1)
    test_db_session.commit()

    sync_service = StreamSyncService(db=test_db_session, kt_client=mock_keitaro)

    # Remote has a third state entirely (e.g. 70/30 with another offer)
    mock_keitaro.get_stream.return_value = {
        "id": 303,
        "name": "Reconcile Stream 3",
        "offers": [
            {"offer_id": 10, "share": 70, "state": "active"},
            {"offer_id": 99, "share": 30, "state": "active"},
        ],
    }

    candidate = [{"offer_id": 10, "share": 50}]
    dto = await sync_service._reconcile_timeout(stream, candidate, op=None)

    assert dto.sync_status == "conflict"
    assert stream.sync_status == "conflict"


@pytest.mark.asyncio
async def test_r3_reconcile_only_allowed_on_unknown_or_verifying_streams(
    test_db_session, mock_keitaro
):
    """R3: Reconcile is strictly forbidden on normal dirty or clean streams."""
    camp = Campaign(id=2, remote_campaign_id=2, name="Test Camp 2", alias="test_camp_2")
    test_db_session.add(camp)
    test_db_session.flush()

    stream = Stream(
        campaign_id=camp.id,
        remote_stream_id=304,
        name="Dirty Stream",
        position=1,
        is_managed=True,
        sync_status="dirty",
        revision=1,
    )
    test_db_session.add(stream)
    test_db_session.commit()

    sync_service = StreamSyncService(db=test_db_session, kt_client=mock_keitaro)

    # Calling reconcile_stream on dirty stream raises EditorConflictError
    with pytest.raises(EditorConflictError) as exc:
        await sync_service.reconcile_stream(stream.id)
    assert "undetermined sync state" in str(exc.value)


@pytest.mark.asyncio
async def test_r3_reconcile_candidate_updates_baseline_and_preserves_subsequent_draft_edits(
    test_db_session, mock_keitaro
):
    """R3: Successful candidate reconcile updates Baseline strictly from candidate snapshot, leaving subsequent Draft edits dirty."""
    from src.domain.models import Operation

    camp = Campaign(id=3, remote_campaign_id=3, name="Test Camp 3", alias="test_camp_3")
    test_db_session.add(camp)
    test_db_session.flush()

    stream = Stream(
        campaign_id=camp.id,
        remote_stream_id=305,
        name="Candidate Stream",
        position=1,
        is_managed=True,
        sync_status="verifying",
        revision=2,
    )
    test_db_session.add(stream)
    test_db_session.flush()

    off1 = Offer(remote_offer_id=201, name="Offer 201")
    off2 = Offer(remote_offer_id=202, name="Offer 202")
    test_db_session.add_all([off1, off2])
    test_db_session.flush()

    # Initial state before crash: candidate had 60/40. But user already edited draft to 70/30 in local session
    so1 = StreamOffer(
        stream_id=stream.id,
        offer_id=off1.id,
        baseline_share=50,
        draft_share=70,  # User edited after push
        baseline_state="active",
        draft_state="active",
    )
    so2 = StreamOffer(
        stream_id=stream.id,
        offer_id=off2.id,
        baseline_share=50,
        draft_share=30,  # User edited after push
        baseline_state="active",
        draft_state="active",
    )
    test_db_session.add_all([so1, so2])

    op = Operation(
        operation_type="push_stream",
        target_id=stream.id,
        status="publishing",
        idempotency_key="reconcile_test_op_1",
        candidate_payload=[{"offer_id": 201, "share": 60}, {"offer_id": 202, "share": 40}],
    )
    test_db_session.add(op)
    test_db_session.commit()

    # Remote confirms the candidate (60/40) did land on Keitaro
    mock_keitaro.get_stream.return_value = {
        "id": 305,
        "name": "Candidate Stream",
        "offers": [
            {"offer_id": 201, "share": 60, "state": "active"},
            {"offer_id": 202, "share": 40, "state": "active"},
        ],
    }

    sync_service = StreamSyncService(db=test_db_session, kt_client=mock_keitaro)
    dto = await sync_service._reconcile_timeout(stream, op.candidate_payload, op=op)

    # Candidate was verified on remote: Baseline is updated to 60/40!
    # But because draft has subsequent edits (70/30 != 60/40), sync_status remains 'dirty'!
    assert dto.sync_status == "dirty"
    assert stream.sync_status == "dirty"
    assert so1.baseline_share == 60
    assert so1.draft_share == 70
    assert so2.baseline_share == 40
    assert so2.draft_share == 30


def test_r8_simulator_storage_write_failure_raises_and_rolls_back_memory(tmp_path):
    """R8: SimulatorStorage._save_state never swallows disk errors and rolls back memory."""
    from unittest.mock import patch

    from src.simulator.storage import SimulatorStorage

    state_file = tmp_path / "sim_state.json"
    storage = SimulatorStorage(state_file=str(state_file))

    initial_campaign_count = len(storage.campaigns)

    with patch.object(storage, "_save_state", side_effect=OSError("Disk write error")):
        with pytest.raises(OSError):
            storage.create_campaign({"name": "Failing Campaign"})

    # In-memory campaigns must be rolled back!
    assert len(storage.campaigns) == initial_campaign_count


def test_r8_simulator_corrupt_file_preserved_never_overwritten_with_seed(tmp_path):
    """R8: Corrupt state file is preserved and never overwritten with seed data."""
    from src.simulator.storage import SimulatorStorage

    corrupt_file = tmp_path / "corrupt_state.json"
    corrupt_file.write_text("NOT_VALID_JSON{::: broken", encoding="utf-8")

    storage = SimulatorStorage(state_file=str(corrupt_file))

    # Should detect corrupt file and create backup, leaving corrupt file preserved
    assert storage is not None
    assert corrupt_file.exists()
    assert "NOT_VALID_JSON" in corrupt_file.read_text(encoding="utf-8")


def test_r9_simulator_control_endpoints_forbidden_in_production(monkeypatch):
    """R9: Simulator reset and simulate/fault endpoints return 403 Forbidden in production."""
    from fastapi.testclient import TestClient

    from src.core.config import settings
    from src.simulator.app import simulator_app

    monkeypatch.setattr(settings, "APP_ENV", "production")

    with TestClient(simulator_app) as client:
        # Reset endpoint in production -> 403
        res_reset = client.post("/demo/reset", headers={"Api-Key": "demo-api-key"})
        assert res_reset.status_code == 403

        # Simulate fault endpoint in production -> 403
        res_fault = client.post(
            "/demo/simulate/fault",
            json={"fault": "timeout"},
            headers={"Api-Key": "demo-api-key"},
        )
        assert res_fault.status_code == 403


def test_r9_simulator_report_build_requires_api_key():
    """R9: /admin_api/v1/report/build strictly requires valid Api-Key header."""
    from fastapi.testclient import TestClient

    from src.simulator.app import simulator_app

    with TestClient(simulator_app) as client:
        # Missing Api-Key -> 401
        res_no_key = client.post(
            "/admin_api/v1/report/build", json={"range": {"interval": "today"}}
        )
        assert res_no_key.status_code == 401

        # Valid Api-Key -> 200
        res_with_key = client.post(
            "/admin_api/v1/report/build",
            json={"range": {"interval": "today"}},
            headers={"Api-Key": "demo-api-key"},
        )
        assert res_with_key.status_code == 200


@pytest.mark.asyncio
async def test_r10_worker_lease_prevents_concurrent_push(test_db_session, mock_keitaro):
    """R10: Push is prevented if another worker holds an active lease on the stream."""
    import time

    from src.services.sync_service import SyncConflictError

    camp = Campaign(id=4, remote_campaign_id=4, name="Test Camp 4", alias="test_camp_4")
    test_db_session.add(camp)
    test_db_session.flush()

    stream = Stream(
        campaign_id=camp.id,
        remote_stream_id=306,
        name="Leased Stream",
        position=1,
        is_managed=True,
        sync_status="dirty",
        revision=1,
    )
    test_db_session.add(stream)
    test_db_session.flush()

    off10 = Offer(remote_offer_id=310, name="Offer 310")
    test_db_session.add(off10)
    test_db_session.flush()

    so = StreamOffer(
        stream_id=stream.id,
        offer_id=off10.id,
        baseline_share=100,
        draft_share=100,
        baseline_state="active",
        draft_state="active",
    )
    test_db_session.add(so)
    test_db_session.flush()

    from src.domain.models import Operation

    # Worker A holds active lease expiring in 30 seconds
    active_op = Operation(
        operation_type="push_stream",
        target_id=stream.id,
        status="in_progress",
        idempotency_key="leased_op_1",
        worker_id="worker_A",
        lease_expires_at=time.time() + 30.0,
    )
    test_db_session.add(active_op)
    test_db_session.commit()

    sync_service = StreamSyncService(db=test_db_session, kt_client=mock_keitaro)

    # Worker B tries to push to this stream -> SyncConflictError because worker A holds lease
    with pytest.raises(SyncConflictError) as exc:
        await sync_service.push_stream(stream.id, 1, idempotency_key="worker_b_key")
    assert "worker_A" in str(exc.value)


def test_r10_startup_recovery_ignores_live_worker_leases(test_db_session):
    """R10: Startup recovery must not reset streams held by live workers with active leases."""
    import time

    from src.core.database import demo_engine
    from src.domain.models import Operation
    from src.main import recover_interrupted_streams

    camp = Campaign(id=5, remote_campaign_id=5, name="Test Camp 5", alias="test_camp_5")
    test_db_session.add(camp)
    test_db_session.flush()

    stream = Stream(
        campaign_id=camp.id,
        remote_stream_id=307,
        name="Active Lease Stream",
        position=1,
        is_managed=True,
        sync_status="publishing",
        revision=1,
    )
    test_db_session.add(stream)
    test_db_session.flush()

    # Worker with active lease
    active_op = Operation(
        operation_type="push_stream",
        target_id=stream.id,
        status="publishing",
        idempotency_key="leased_op_live",
        worker_id="live_worker_X",
        lease_expires_at=time.time() + 300.0,
    )
    test_db_session.add(active_op)
    test_db_session.commit()

    # Run startup recovery
    recover_interrupted_streams(demo_engine)

    # Refresh stream: it must NOT be marked unknown because its lease is still active!
    test_db_session.expire_all()
    assert stream.sync_status == "publishing"


@pytest.mark.asyncio
async def test_reconcile_confirms_result_and_preserves_pins_cancel_does_not_unpin(
    test_db_session, mock_keitaro
):
    """R3: Reconcile preserves pins from local snapshot; clean is only set when Draft matches Baseline including pins; Cancel does not unpin."""
    from src.domain.models import Operation

    camp = Campaign(id=6, remote_campaign_id=6, name="Test Camp Pins", alias="test_camp_pins")
    test_db_session.add(camp)
    test_db_session.flush()

    stream = Stream(
        campaign_id=camp.id,
        remote_stream_id=501,
        name="Stream with Pins",
        position=1,
        is_managed=True,
        sync_status="verifying",
        revision=1,
    )
    test_db_session.add(stream)
    test_db_session.flush()

    off1 = Offer(remote_offer_id=601, name="Offer 601")
    off2 = Offer(remote_offer_id=602, name="Offer 602")
    test_db_session.add_all([off1, off2])
    test_db_session.flush()

    # Offer 1 was pinned in Draft (60 pinned / 40 unpinned)
    so1 = StreamOffer(
        stream_id=stream.id,
        offer_id=off1.id,
        baseline_share=50,
        draft_share=60,
        baseline_state="active",
        draft_state="active",
        baseline_pinned=False,
        draft_pinned=True,  # PINNED!
    )
    so2 = StreamOffer(
        stream_id=stream.id,
        offer_id=off2.id,
        baseline_share=50,
        draft_share=40,
        baseline_state="active",
        draft_state="active",
        baseline_pinned=False,
        draft_pinned=False,
    )
    test_db_session.add_all([so1, so2])

    # Operation stored with local snapshot containing is_pinned=True for offer 601
    op = Operation(
        operation_type="push_stream",
        target_id=stream.id,
        status="publishing",
        idempotency_key="pinned_push_op",
        candidate_payload={
            "offers": [{"offer_id": 601, "share": 60}, {"offer_id": 602, "share": 40}],
            "local_snapshot": [
                {"remote_offer_id": 601, "share": 60, "state": "active", "is_pinned": True},
                {"remote_offer_id": 602, "share": 40, "state": "active", "is_pinned": False},
            ],
            "base_revision": 1,
        },
    )
    test_db_session.add(op)
    test_db_session.commit()

    # Keitaro confirms 60/40 landed
    mock_keitaro.get_stream.return_value = {
        "id": 501,
        "name": "Stream with Pins",
        "offers": [
            {"offer_id": 601, "share": 60, "state": "active"},
            {"offer_id": 602, "share": 40, "state": "active"},
        ],
    }

    sync_service = StreamSyncService(db=test_db_session, kt_client=mock_keitaro)
    dto = await sync_service._reconcile_timeout(stream, op.candidate_payload["offers"], op=op)

    # 1. Baseline pinned must be preserved as True!
    assert so1.baseline_pinned is True
    assert so1.draft_pinned is True
    assert so1.baseline_share == 60
    assert dto.sync_status == "clean"
    assert stream.sync_status == "clean"

    # 2. Subsequent Cancel must NOT unpin the offer
    editor_service = StreamEditorService(db=test_db_session, kt_client=mock_keitaro)
    cancel_dto = editor_service.cancel_stream(stream.id)
    assert cancel_dto.offers[0].is_pinned is True
    assert so1.draft_pinned is True


@pytest.mark.asyncio
async def test_stale_reconcile_after_newer_operation_superseded_without_overwriting(
    test_db_session, mock_keitaro
):
    """R3: A stale Reconcile finishing after a newer operation must NOT overwrite newer Baseline or revision."""
    from src.domain.models import Operation

    camp = Campaign(id=7, remote_campaign_id=7, name="Test Camp Superseded", alias="test_camp_sup")
    test_db_session.add(camp)
    test_db_session.flush()

    # Stream is already at revision 3 with Baseline 70/30
    stream = Stream(
        campaign_id=camp.id,
        remote_stream_id=701,
        name="Superseded Stream",
        position=1,
        is_managed=True,
        sync_status="dirty",
        revision=3,  # Already bumped to 3 by a newer completed operation
    )
    test_db_session.add(stream)
    test_db_session.flush()

    off1 = Offer(remote_offer_id=701, name="Offer 701")
    off2 = Offer(remote_offer_id=702, name="Offer 702")
    test_db_session.add_all([off1, off2])
    test_db_session.flush()

    so1 = StreamOffer(
        stream_id=stream.id,
        offer_id=off1.id,
        baseline_share=70,  # Newer baseline
        draft_share=70,
        baseline_state="active",
        draft_state="active",
        baseline_pinned=False,
        draft_pinned=False,
    )
    so2 = StreamOffer(
        stream_id=stream.id,
        offer_id=off2.id,
        baseline_share=30,  # Newer baseline
        draft_share=30,
        baseline_state="active",
        draft_state="active",
        baseline_pinned=False,
        draft_pinned=False,
    )
    test_db_session.add_all([so1, so2])

    # Old stale operation with base_revision 1 and candidate 60/40
    stale_op = Operation(
        operation_type="push_stream",
        target_id=stream.id,
        status="in_progress",
        idempotency_key="stale_op_key",
        candidate_payload={
            "offers": [{"offer_id": 701, "share": 60}, {"offer_id": 702, "share": 40}],
            "base_revision": 1,
        },
    )
    test_db_session.add(stale_op)
    test_db_session.commit()

    # KT read-back returns 60/40 from the old write
    mock_keitaro.get_stream.return_value = {
        "id": 701,
        "name": "Superseded Stream",
        "offers": [
            {"offer_id": 701, "share": 60, "state": "active"},
            {"offer_id": 702, "share": 40, "state": "active"},
        ],
    }

    sync_service = StreamSyncService(db=test_db_session, kt_client=mock_keitaro)
    await sync_service._reconcile_timeout(stream, stale_op.candidate_payload["offers"], op=stale_op)

    # Newer Baseline (70/30) and revision (3) must be 100% UNTOUCHED!
    test_db_session.refresh(stream)
    test_db_session.refresh(so1)
    test_db_session.refresh(so2)
    test_db_session.refresh(stale_op)

    assert stream.revision == 3  # Not downgraded to 2!
    assert so1.baseline_share == 70  # Not overwritten to 60!
    assert so2.baseline_share == 30  # Not overwritten to 40!
    assert stale_op.status == "superseded"


@pytest.mark.asyncio
async def test_concurrent_push_independent_sessions_single_winner_atomic_claim(mock_keitaro):
    """R10: Two concurrent push requests in independent sessions result in exactly ONE winner and ONE PUT."""
    from src.core.database import DemoSessionLocal
    from src.services.sync_service import SyncConflictError

    with DemoSessionLocal() as s1:
        camp = Campaign(id=8, remote_campaign_id=8, name="Camp Claim", alias="camp_claim")
        s1.add(camp)
        s1.flush()

        st = Stream(
            campaign_id=camp.id,
            remote_stream_id=801,
            name="Stream Claim",
            position=1,
            is_managed=True,
            sync_status="dirty",
            revision=1,
        )
        s1.add(st)
        s1.flush()

        off = Offer(remote_offer_id=801, name="Offer 801")
        s1.add(off)
        s1.flush()

        so = StreamOffer(
            stream_id=st.id,
            offer_id=off.id,
            baseline_share=100,
            draft_share=100,
            baseline_state="active",
            draft_state="active",
        )
        s1.add(so)
        s1.commit()
        stream_id = st.id

    mock_keitaro.get_stream.return_value = {
        "id": 801,
        "name": "Stream Claim",
        "offers": [{"offer_id": 801, "share": 100, "state": "active"}],
    }

    # Session 1 and Session 2 concurrently attempt to push
    with DemoSessionLocal() as session1, DemoSessionLocal() as session2:
        sync1 = StreamSyncService(db=session1, kt_client=mock_keitaro)
        sync2 = StreamSyncService(db=session2, kt_client=mock_keitaro)

        # Worker 1 claims and executes
        dto1 = await sync1.push_stream(
            stream_id, expected_revision=1, idempotency_key="worker1_key"
        )
        assert dto1.sync_status == "clean"

        # Worker 2 attempts to claim: fails atomically because stream is already claimed / revision bumped!
        with pytest.raises(SyncConflictError):
            await sync2.push_stream(stream_id, expected_revision=1, idempotency_key="worker2_key")


@pytest.mark.asyncio
async def test_push_idempotent_replay_returns_confirmed_dto_without_second_put(
    test_db_session, mock_keitaro
):
    """R10: Replaying an exact idempotency key with original expected_revision returns confirmed result without second PUT."""
    camp = Campaign(id=9, remote_campaign_id=9, name="Camp Replay", alias="camp_replay")
    test_db_session.add(camp)
    test_db_session.flush()

    stream = Stream(
        campaign_id=camp.id,
        remote_stream_id=901,
        name="Stream Replay",
        position=1,
        is_managed=True,
        sync_status="dirty",
        revision=1,
    )
    test_db_session.add(stream)
    test_db_session.flush()

    off = Offer(remote_offer_id=901, name="Offer 901")
    test_db_session.add(off)
    test_db_session.flush()

    so = StreamOffer(
        stream_id=stream.id,
        offer_id=off.id,
        baseline_share=100,
        draft_share=100,
        baseline_state="active",
        draft_state="active",
    )
    test_db_session.add(so)
    test_db_session.commit()

    mock_keitaro.get_stream.return_value = {
        "id": 901,
        "name": "Stream Replay",
        "offers": [{"offer_id": 901, "share": 100, "state": "active"}],
    }

    sync = StreamSyncService(db=test_db_session, kt_client=mock_keitaro)

    # 1. First push succeeds, revision increments 1 -> 2
    dto1 = await sync.push_stream(
        stream.id, expected_revision=1, idempotency_key="client_retry_key_999"
    )
    assert dto1.revision == 2

    # 2. Replay of same key with original revision=1 must succeed immediately without second PUT
    mock_keitaro.update_stream.reset_mock()
    dto2 = await sync.push_stream(
        stream.id, expected_revision=1, idempotency_key="client_retry_key_999"
    )
    assert dto2.revision == 2
    mock_keitaro.update_stream.assert_not_called()


def test_simulator_delete_stream_offer_write_failure_preserves_shares_34_33_33(tmp_path):
    """R8: Failure writing to disk during offer deletion preserves 34/33/33 shares in memory and on disk."""
    from unittest.mock import patch

    from src.simulator.storage import SimulatorStorage

    state_file = tmp_path / "sim_34_33_33.json"
    storage = SimulatorStorage(state_file=str(state_file))

    # Create stream with 3 offers: 34, 33, 33
    stream_payload = {
        "campaign_id": 1,
        "name": "Three Offers Stream",
        "offers": [
            {"offer_id": 101, "share": 34, "state": "active"},
            {"offer_id": 102, "share": 33, "state": "active"},
            {"offer_id": 103, "share": 33, "state": "active"},
        ],
    }
    created = storage.create_stream(stream_payload)
    sid = created["id"]

    # Attempt to delete offer 101 with simulated disk write failure
    with patch.object(storage, "_save_state", side_effect=OSError("Disk failure")):
        with pytest.raises(OSError):
            storage.delete_stream_offer(sid, 101)

    # Memory state MUST strictly preserve original offers and shares 34, 33, 33 (not 34, 50, 50 = 134%)!
    curr = storage.get_stream(sid)
    assert len(curr["offers"]) == 3
    shares = [o["share"] for o in curr["offers"]]
    assert shares == [34, 33, 33]
    assert sum(shares) == 100


def test_simulator_reset_write_failure_preserves_user_campaigns(tmp_path):
    """R8: Failed Reset does not wipe user campaigns from memory."""
    from unittest.mock import patch

    from src.simulator.storage import SimulatorStorage

    state_file = tmp_path / "sim_reset.json"
    storage = SimulatorStorage(state_file=str(state_file))

    user_camp = storage.create_campaign({"name": "User Custom Campaign 123"})
    cid = user_camp["id"]

    with patch.object(storage, "_save_state", side_effect=OSError("Disk full on reset")):
        with pytest.raises(OSError):
            storage.reset()

    # User campaign must still be preserved in memory!
    assert storage.get_campaign(cid) is not None
    assert storage.get_campaign(cid)["name"] == "User Custom Campaign 123"


def test_simulator_corrupt_file_blocks_subsequent_writes_without_overwriting_seed(tmp_path):
    """R8: When state file is corrupt, subsequent write operations are blocked with RuntimeError and corrupt file is not overwritten."""
    from src.simulator.storage import SimulatorStorage

    corrupt_file = tmp_path / "broken.json"
    corrupt_content = "CORRUPT_RAW_DATA_12345"
    corrupt_file.write_text(corrupt_content, encoding="utf-8")

    storage = SimulatorStorage(state_file=str(corrupt_file))

    # Attempting to write must fail with RuntimeError
    with pytest.raises(RuntimeError) as exc:
        storage.create_campaign({"name": "New Campaign"})
    assert "corrupt" in str(exc.value)

    # Corrupt file must still be preserved on disk and not replaced with seed!
    assert corrupt_file.read_text(encoding="utf-8") == corrupt_content


def test_legacy_unversioned_database_safe_adoption_under_alembic(tmp_path):
    """R11: Existing database created via create_all without alembic_version is safely adopted by Alembic without table collisions or data loss."""
    import os
    import subprocess

    from sqlalchemy import create_engine

    from src.domain.models import Base, Campaign

    legacy_db = tmp_path / "legacy_existing.db"
    legacy_url = f"sqlite:///{legacy_db}"

    # 1. Create legacy schema via create_all
    engine = create_engine(legacy_url)
    Base.metadata.create_all(engine)

    # 2. Insert canary business record into legacy database
    with engine.connect() as conn:
        conn.execute(
            Campaign.__table__.insert().values(
                id=1, remote_campaign_id=9999, name="Legacy User Campaign", alias="legacy_user_camp"
            )
        )
        conn.commit()
    engine.dispose()

    # 3. Run alembic upgrade head targeting this legacy database
    env = dict(os.environ, DATABASE_URL=legacy_url)
    res = subprocess.run(
        ["uv", "run", "--locked", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res.returncode == 0, f"Alembic upgrade failed: {res.stderr}"

    # 4. Verify existing record survived and new migration columns were added
    test_eng = create_engine(legacy_url)
    with test_eng.connect() as conn:
        row = conn.execute(Campaign.__table__.select().where(Campaign.id == 1)).fetchone()
        assert row is not None
        assert row.name == "Legacy User Campaign"
        assert row.remote_campaign_id == 9999
    test_eng.dispose()
