"""Simulator form saves must also work for streams received from Creator."""

import copy

import pytest
from fastapi.testclient import TestClient

from src.simulator.app import simulator_app
from src.simulator.storage import SimulatorStorage, compute_stream_fingerprint


def storage_with_creator_streams(tmp_path):
    storage = SimulatorStorage(state_file=str(tmp_path / "simulator.json"))
    # Reuse an existing seed campaign; never create a campaign or call a tracker.
    storage.streams = {
        20: {
            "id": 20,
            "campaign_id": 3,
            "name": "Flow 1",
            "type": "regular",
            "schema": "redirect",
            "action_payload": "https://google.com/",
            "offers": [],
            "filters": [],
            "position": 1,
        },
        21: {
            "id": 21,
            "campaign_id": 3,
            "name": "Flow 2",
            "type": "default",
            "schema": "landings",
            "offers": [
                {"offer_id": 101, "share": 50, "state": "active"},
                {"offer_id": 102, "share": 50, "state": "active"},
            ],
            "filters": [],
            "landings": [],
            "position": 2,
        },
    }
    storage._stream_id_seq = 22
    return storage


def test_creator_stream_can_be_saved_without_simulator_restart(tmp_path):
    storage = storage_with_creator_streams(tmp_path)
    payload = storage.streams.pop(21)
    payload.pop("id")
    created = storage.create_stream(payload)
    assert created.get("role") == "flow_2"
    saved = storage.update_stream_offers(
        3,
        created["id"],
        [{"offer_id": 101, "share": 100, "state": "active"}],
        compute_stream_fingerprint(created),
    )
    assert saved["offers"][0]["share"] == 100


def test_legacy_creator_stream_save_persists_roles_without_reset(tmp_path):
    storage = storage_with_creator_streams(tmp_path)
    flow1_before = copy.deepcopy(storage.streams[20])
    saved = storage.update_stream_offers(
        3,
        21,
        [{"offer_id": 101, "share": 100, "state": "active"}],
        compute_stream_fingerprint(storage.streams[21]),
    )
    assert saved["role"] == "flow_2"
    reloaded = SimulatorStorage(state_file=storage.state_file)
    assert reloaded.get_stream(21)["offers"] == saved["offers"]
    assert reloaded.get_stream(21)["role"] == "flow_2"
    assert {
        k: v
        for k, v in reloaded.get_stream(20).items()
        if k not in ("role", "fingerprint", "clicks_today")
    } == flow1_before
    assert len(reloaded.get_campaigns()) == 3
    assert len(reloaded.get_offers()) == 5


@pytest.mark.parametrize("invalid_target", ["redirect", "ambiguous"])
def test_role_recovery_rejects_redirect_and_ambiguous_streams(tmp_path, invalid_target):
    storage = storage_with_creator_streams(tmp_path)
    if invalid_target == "redirect":
        storage.streams[20]["name"] = "Flow 2"
        target = 20
    else:
        storage.streams[22] = {**copy.deepcopy(storage.streams[21]), "id": 22}
        target = 21
    before = copy.deepcopy(storage.streams)
    with pytest.raises(ValueError, match="Только основной поток"):
        storage.update_stream_offers(3, target, [{"offer_id": 101, "share": 100}])
    assert storage.streams == before


def test_role_recovery_preserves_conflict_and_disk_failure_guards(tmp_path, monkeypatch):
    storage = storage_with_creator_streams(tmp_path)
    before = copy.deepcopy(storage.streams)
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        storage.update_stream_offers(
            3,
            21,
            [{"offer_id": 101, "share": 100}],
            "outdated-fingerprint",
        )
    assert storage.streams == before

    def disk_failure():
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(storage, "_save_state", disk_failure)
    with pytest.raises(OSError, match="synthetic disk failure"):
        storage.update_stream_offers(3, 21, [{"offer_id": 101, "share": 100}])
    assert storage.streams == before


def test_simulator_api_removes_and_adds_offers_in_legacy_creator_stream(tmp_path, monkeypatch):
    storage = storage_with_creator_streams(tmp_path)
    monkeypatch.setattr("src.simulator.app.simulator_storage", storage)
    monkeypatch.setattr("src.simulator.app.settings.DEMO_KEITARO_API_KEY", "demo-api-key")
    headers = {"Api-Key": "demo-api-key"}
    path = "/demo/admin/campaigns/3/streams/21/offers"
    with TestClient(simulator_app) as client:
        stream = client.get("/admin_api/v1/streams/21", headers=headers).json()
        removed = client.put(
            path,
            headers=headers,
            json={
                "expected_fingerprint": stream["fingerprint"],
                "offers": [{"offer_id": 101, "share": 100}],
            },
        )
        assert removed.status_code == 200, removed.text
        restored = client.put(
            path,
            headers=headers,
            json={
                "expected_fingerprint": removed.json()["fingerprint"],
                "offers": [{"offer_id": 101, "share": 50}, {"offer_id": 102, "share": 50}],
            },
        )
        assert restored.status_code == 200, restored.text
        read_back = client.get("/admin_api/v1/streams/21", headers=headers)
        assert read_back.json()["offers"] == restored.json()["offers"]
    reloaded = SimulatorStorage(state_file=storage.state_file)
    assert reloaded.get_stream(21)["offers"] == restored.json()["offers"]
