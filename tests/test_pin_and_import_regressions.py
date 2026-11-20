import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.core.database import Base
from src.domain.models import Stream, StreamOffer
from src.services.editor_service import StreamEditorService
from src.services.keitaro_client import KeitaroGateway


class MockKT(KeitaroGateway):
    def __init__(self):
        self.put_calls = []
        self.campaigns = {100: {"id": 100, "name": "test_rykov_pin", "alias": "pin-100"}}
        self.streams = {
            1001: {
                "id": 1001,
                "campaign_id": 100,
                "name": "Flow 2 (Offers)",
                "type": "default",
                "schema": "landings",
                "offers": [
                    {"offer_id": 201, "share": 60, "state": "active"},
                    {"offer_id": 202, "share": 30, "state": "active"},
                    {"offer_id": 203, "share": 10, "state": "active"},
                ],
            }
        }
        self.offers = {
            201: {"id": 201, "name": "Offer 201"},
            202: {"id": 202, "name": "Offer 202"},
            203: {"id": 203, "name": "Offer 203"},
        }

    async def get_campaign(self, campaign_id: int):
        return self.campaigns.get(campaign_id)

    async def get_campaign_streams(self, campaign_id: int):
        return [s for s in self.streams.values() if s.get("campaign_id") == campaign_id]

    async def get_stream(self, stream_id: int):
        return self.streams.get(stream_id)

    async def get_offer(self, offer_id: int):
        return self.offers.get(offer_id, {"id": offer_id, "name": f"Offer {offer_id}"})

    async def update_stream(self, stream_id: int, payload: dict):
        self.put_calls.append({"stream_id": stream_id, "payload": payload})
        if stream_id in self.streams:
            self.streams[stream_id]["offers"] = payload.get("offers", [])
        return self.streams.get(stream_id)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.mark.asyncio
async def test_pin_without_share_change_preserves_60_30_10(db_session):
    """Regress quality-1-13: [60, 30, 10] + Pin on first offer must NOT change other shares to [60, 20, 20]."""
    mock_kt = MockKT()
    editor = StreamEditorService(db_session, mock_kt)

    # 1. Initial fetch: streams have 60, 30, 10
    detail = await editor.fetch_campaign_streams(100)
    managed_stream = next(s for s in detail.streams if s.is_managed)
    offers = managed_stream.offers
    assert [o.share for o in offers] == [60, 30, 10]
    assert all(not o.is_pinned for o in offers)

    # 2. Pin first offer without new share (share=None, is_pinned=True)
    updated = await editor.update_offer_weight(
        stream_id=managed_stream.id,
        offer_id=201,
        share=None,
        is_pinned=True,
        expected_revision=managed_stream.revision,
    )

    # 3. Verify shares are strictly preserved as 60/30/10, not 60/20/20!
    updated_offers = updated.offers
    assert [o.share for o in updated_offers] == [60, 30, 10]
    assert updated_offers[0].is_pinned is True
    assert updated_offers[1].is_pinned is False
    assert updated_offers[2].is_pinned is False

    # 4. Local pin action does NOT perform KT PUT
    assert len(mock_kt.put_calls) == 0


@pytest.mark.asyncio
async def test_pin_unpin_returning_to_baseline_is_clean_after_reload(db_session):
    mock_kt = MockKT()
    for offer, share in zip(mock_kt.streams[1001]["offers"], [34, 33, 33], strict=True):
        offer["share"] = share
    editor = StreamEditorService(db_session, mock_kt)
    stream = (await editor.fetch_campaign_streams(100)).streams[0]

    pinned = await editor.update_offer_weight(stream.id, 201, None, True, stream.revision)
    assert pinned.sync_status == "dirty"
    unpinned = await editor.update_offer_weight(stream.id, 201, None, False, pinned.revision)
    assert [offer.share for offer in unpinned.offers] == [34, 33, 33]
    assert all(not offer.is_pinned for offer in unpinned.offers)
    assert unpinned.sync_status == "clean"
    assert unpinned.revision == pinned.revision + 1

    db_session.expire_all()
    reloaded = StreamEditorService(db_session, mock_kt).get_campaign_detail(100).streams[0]
    assert reloaded.sync_status == "clean"
    # A reverted draft no longer blocks Fetch or requires publishing.
    assert (await editor.fetch_campaign_streams(100)).streams[0].sync_status == "clean"
    assert mock_kt.put_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("difference", ["shares", "other_pin", "baseline_pin", "composition"])
async def test_pin_unpin_keeps_actual_draft_differences_dirty(db_session, difference):
    mock_kt = MockKT()
    if difference != "shares":
        for offer, share in zip(mock_kt.streams[1001]["offers"], [34, 33, 33], strict=True):
            offer["share"] = share
    editor = StreamEditorService(db_session, mock_kt)
    stream = (await editor.fetch_campaign_streams(100)).streams[0]

    if difference in ("other_pin", "baseline_pin"):
        binding = (
            db_session.query(StreamOffer)
            .filter_by(stream_id=stream.id)
            .order_by(StreamOffer.position)
            .all()[1]
        )
        if difference == "other_pin":
            binding.draft_pinned = True
        else:
            binding.baseline_pinned = True
        db_session.query(Stream).filter_by(id=stream.id).first().sync_status = "dirty"
        db_session.commit()
    elif difference == "composition":
        stream = await editor.remove_offer(stream.id, 203, stream.revision)

    pinned = await editor.update_offer_weight(stream.id, 201, None, True, stream.revision)
    unpinned = await editor.update_offer_weight(stream.id, 201, None, False, pinned.revision)
    assert unpinned.sync_status == "dirty"
    assert mock_kt.put_calls == []


@pytest.mark.asyncio
async def test_remove_bring_back_returning_to_baseline_is_clean(db_session):
    mock_kt = MockKT()
    for offer, share in zip(mock_kt.streams[1001]["offers"], [34, 33, 33], strict=True):
        offer["share"] = share
    editor = StreamEditorService(db_session, mock_kt)
    stream = (await editor.fetch_campaign_streams(100)).streams[0]
    removed = await editor.remove_offer(stream.id, 203, stream.revision)
    restored = await editor.bring_back_offer(stream.id, 203, removed.revision)
    assert [offer.share for offer in restored.offers] == [34, 33, 33]
    assert restored.sync_status == "clean"
    assert mock_kt.put_calls == []


@pytest.mark.asyncio
async def test_changing_share_or_unpinning_triggers_recalculation(db_session):
    """Explicit new share value or unpinning correctly redistributes remaining unpinned shares."""
    mock_kt = MockKT()
    editor = StreamEditorService(db_session, mock_kt)

    detail = await editor.fetch_campaign_streams(100)
    managed_stream = next(s for s in detail.streams if s.is_managed)

    # Pin first offer to 50%
    updated = await editor.update_offer_weight(
        stream_id=managed_stream.id,
        offer_id=201,
        share=50,
        is_pinned=True,
        expected_revision=managed_stream.revision,
    )
    # Remaining 50 split between 2 unpinned: 25 / 25
    assert [o.share for o in updated.offers] == [50, 25, 25]
    assert updated.offers[0].is_pinned is True

    # Unpin first offer -> redistributes all 3 unpinned: 100 / 3 -> 34, 33, 33
    unpinned = await editor.update_offer_weight(
        stream_id=managed_stream.id,
        offer_id=201,
        share=None,
        is_pinned=False,
        expected_revision=updated.revision,
    )
    assert [o.share for o in unpinned.offers] == [34, 33, 33]
    assert all(not o.is_pinned for o in unpinned.offers)


@pytest.mark.asyncio
async def test_remote_disabled_offer_marks_stream_read_only(db_session):
    """Regress quality-1-7: Fetching a stream with remote disabled offer does not make it active."""
    mock_kt = MockKT()
    mock_kt.streams[1001]["offers"][1]["state"] = "disabled"

    editor = StreamEditorService(db_session, mock_kt)
    detail = await editor.fetch_campaign_streams(100)
    target_stream = next(s for s in detail.streams if s.remote_stream_id == 1001)

    # Stream with disabled offers must not be editable or must be read-only
    assert target_stream.is_managed is False or target_stream.sync_status == "unsupported"


@pytest.mark.asyncio
async def test_external_stream_schema_change_to_redirect_disables_managed(db_session):
    """Regress quality-1-8: Stream converted externally to redirect schema loses managed status."""
    mock_kt = MockKT()
    editor = StreamEditorService(db_session, mock_kt)

    # First fetch: managed
    detail = await editor.fetch_campaign_streams(100)
    assert any(s.is_managed for s in detail.streams)

    # External change: stream converted to redirect, offers cleared
    mock_kt.streams[1001]["schema"] = "redirect"
    mock_kt.streams[1001]["offers"] = []

    # Second fetch
    detail2 = await editor.fetch_campaign_streams(100)
    stream2 = next(s for s in detail2.streams if s.remote_stream_id == 1001)
    assert stream2.is_managed is False
