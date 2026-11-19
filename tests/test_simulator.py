"""Tests for the standalone Keitaro Admin API simulator."""

import pytest
from starlette.testclient import TestClient

from src.simulator.app import simulator_app
from src.simulator.storage import simulator_storage


@pytest.fixture(autouse=True)
def reset_storage():
    """Ensure clean simulator state before every test."""
    simulator_storage.reset()
    yield
    simulator_storage.reset()


client = TestClient(simulator_app)
AUTH_HEADERS = {"Api-Key": "demo-api-key"}


def test_simulator_health_and_auth():
    # Health check is public
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["service"] == "keitaro_simulator"

    # Admin API requires Api-Key header
    res_no_auth = client.get("/admin_api/v1/domains")
    assert res_no_auth.status_code == 401

    res_auth = client.get("/admin_api/v1/domains", headers=AUTH_HEADERS)
    assert res_auth.status_code == 200
    assert len(res_auth.json()) >= 1


def test_simulator_catalogs_and_seed():
    # 5 Synthetic offers
    res_offers = client.get("/admin_api/v1/offers", headers=AUTH_HEADERS)
    assert res_offers.status_code == 200
    offers = res_offers.json()
    assert len(offers) == 5
    offer_ids = {o["id"] for o in offers}
    assert offer_ids == {101, 102, 103, 104, 105}

    # Group with type=campaigns
    res_groups = client.get(
        "/admin_api/v1/groups", params={"type": "campaigns"}, headers=AUTH_HEADERS
    )
    assert res_groups.status_code == 200
    groups = res_groups.json()
    assert len(groups) >= 1
    assert groups[0]["type"] == "campaigns"

    # Traffic sources with SourceParameters object
    res_sources = client.get("/admin_api/v1/traffic_sources", headers=AUTH_HEADERS)
    assert res_sources.status_code == 200
    sources = res_sources.json()
    assert len(sources) >= 1
    assert "parameters" in sources[0]
    assert isinstance(sources[0]["parameters"], dict)


def test_simulator_streams_and_metadata_preservation():
    # Seed stream 11 has 3 offers with 34/33/33
    res_stream = client.get("/admin_api/v1/streams/11", headers=AUTH_HEADERS)
    assert res_stream.status_code == 200
    stream = res_stream.json()
    assert stream["name"] == "Основной поток (Flow 2)"
    assert stream["schema"] == "landings_offers"
    assert stream["position"] == 2
    assert len(stream["offers"]) == 3
    assert [o["share"] for o in stream["offers"]] == [34, 33, 33]

    # PUT update stream offers
    new_offers = [
        {"offer_id": 101, "share": 50, "state": "active"},
        {"offer_id": 102, "share": 50, "state": "active"},
    ]
    res_put = client.put(
        "/admin_api/v1/streams/11",
        json={"offers": new_offers},
        headers=AUTH_HEADERS,
    )
    assert res_put.status_code == 200
    updated = res_put.json()

    # Preserves metadata while updating offers
    assert updated["name"] == "Основной поток (Flow 2)"
    assert updated["schema"] == "landings_offers"
    assert updated["position"] == 2
    assert len(updated["offers"]) == 2
    assert [o["share"] for o in updated["offers"]] == [50, 50]

    # Subsequent GET reflects updated state
    res_get_again = client.get("/admin_api/v1/streams/11", headers=AUTH_HEADERS)
    assert res_get_again.json()["offers"] == new_offers


def test_simulator_delete_stream_offer_and_rebalance():
    # Delete offer 103 from stream 11
    res_del = client.post(
        "/demo/simulate/delete_stream_offer",
        json={"stream_id": 11, "offer_id": 103},
    )
    assert res_del.status_code == 200
    stream = res_del.json()["stream"]

    # Remaining 2 offers are rebalanced to 50/50 summing to 100%
    assert len(stream["offers"]) == 2
    assert sum(o["share"] for o in stream["offers"]) == 100
    assert [o["share"] for o in stream["offers"]] == [50, 50]

    # Global catalog offer 103 is NOT deleted
    res_offer = client.get("/admin_api/v1/offers/103", headers=AUTH_HEADERS)
    assert res_offer.status_code == 200
    assert res_offer.json()["id"] == 103


def test_simulator_disable_global_offer():
    # Initially offer 105 is found
    res1 = client.get("/admin_api/v1/offers/105", headers=AUTH_HEADERS)
    assert res1.status_code == 200

    # Disable offer 105 in catalog
    res_disable = client.post(
        "/demo/simulate/disable_global_offer",
        json={"offer_id": 105, "available": False},
    )
    assert res_disable.status_code == 200

    # Now GET returns 404
    res2 = client.get("/admin_api/v1/offers/105", headers=AUTH_HEADERS)
    assert res2.status_code == 404


def test_simulator_fault_injection():
    # Set fault: reject_update
    client.post(
        "/demo/simulate/fault",
        json={"fault": "reject_update", "params": {"status_code": 422, "message": "Limit hit"}},
    )
    res_put = client.put(
        "/admin_api/v1/streams/11",
        json={"offers": []},
        headers=AUTH_HEADERS,
    )
    assert res_put.status_code == 422
    assert "Limit hit" in res_put.text

    # Clear fault
    client.post("/demo/simulate/fault", json={"fault": "none"})
    res_put_ok = client.put(
        "/admin_api/v1/streams/11",
        json={"offers": [{"offer_id": 101, "share": 100, "state": "active"}]},
        headers=AUTH_HEADERS,
    )
    assert res_put_ok.status_code == 200


def test_simulator_reset():
    # Modify state
    client.post(
        "/demo/simulate/delete_stream_offer",
        json={"stream_id": 11, "offer_id": 101},
    )
    assert len(simulator_storage.get_stream(11)["offers"]) == 2

    # Reset
    res_reset = client.post("/demo/reset")
    assert res_reset.status_code == 200
    # Seed offers restored: 3 offers (34/33/33)
    stream = simulator_storage.get_stream(11)
    assert len(stream["offers"]) == 3
    assert [o["share"] for o in stream["offers"]] == [34, 33, 33]
