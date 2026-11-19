"""Storage and state management for autonomous Keitaro simulator."""

import copy
import json
import os
import threading
from typing import Any

from src.core.config import settings


def _calculate_fair_shares(num_offers: int) -> list[int]:
    """Calculate integer shares summing strictly to 100%."""
    if num_offers <= 0:
        return []
    base_share, remainder = divmod(100, num_offers)
    return [base_share + (1 if i < remainder else 0) for i in range(num_offers)]


class SimulatorStorage:
    """Thread-safe state store for Keitaro Admin API simulation."""

    def __init__(self, surname: str | None = None, state_file: str | None = None):
        self._lock = threading.Lock()
        self.surname = (surname or settings.CAMPAIGN_SURNAME).lower()
        self.active_fault: dict[str, Any] | None = None
        if state_file is not None:
            self.state_file = state_file
        elif "PYTEST_CURRENT_TEST" in os.environ:
            self.state_file = None
        else:
            self.state_file = os.path.join(os.getcwd(), "data", "simulator_state.json")

        if not self._load_state():
            self._init_seed()
            self._save_state()

    def _save_state(self) -> None:
        if not self.state_file:
            return
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.state_file)), exist_ok=True)
            data = {
                "offers": self.offers,
                "domains": self.domains,
                "groups": self.groups,
                "traffic_sources": self.traffic_sources,
                "campaigns": self.campaigns,
                "streams": self.streams,
                "_campaign_id_seq": self._campaign_id_seq,
                "_stream_id_seq": self._stream_id_seq,
            }
            tmp_file = f"{self.state_file}.tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_file, self.state_file)
        except Exception:
            pass

    def _load_state(self) -> bool:
        if not self.state_file or not os.path.exists(self.state_file):
            return False
        try:
            with open(self.state_file, encoding="utf-8") as f:
                data = json.load(f)
            self.offers = {int(k): v for k, v in data.get("offers", {}).items()}
            self.domains = {int(k): v for k, v in data.get("domains", {}).items()}
            self.groups = {int(k): v for k, v in data.get("groups", {}).items()}
            self.traffic_sources = {int(k): v for k, v in data.get("traffic_sources", {}).items()}
            self.campaigns = {int(k): v for k, v in data.get("campaigns", {}).items()}
            self.streams = {int(k): v for k, v in data.get("streams", {}).items()}
            self._campaign_id_seq = data.get("_campaign_id_seq", 2)
            self._stream_id_seq = data.get("_stream_id_seq", 12)
            return True
        except Exception:
            return False

    def _init_seed(self) -> None:
        """Seed deterministic entities per DEMO_MODE_TASK.md."""
        self.offers: dict[int, dict[str, Any]] = {
            101: {
                "id": 101,
                "name": "Demo Offer 1 (Gambling CPA)",
                "status": "active",
                "state": "active",
                "is_available": True,
                "payout_value": 50.0,
                "payout_currency": "USD",
            },
            102: {
                "id": 102,
                "name": "Demo Offer 2 (Finance CPL)",
                "status": "active",
                "state": "active",
                "is_available": True,
                "payout_value": 25.0,
                "payout_currency": "USD",
            },
            103: {
                "id": 103,
                "name": "Demo Offer 3 (Crypto RevShare)",
                "status": "active",
                "state": "active",
                "is_available": True,
                "payout_value": 150.0,
                "payout_currency": "USD",
            },
            104: {
                "id": 104,
                "name": "Demo Offer 4 (Nutra COD)",
                "status": "active",
                "state": "active",
                "is_available": True,
                "payout_value": 30.0,
                "payout_currency": "EUR",
            },
            105: {
                "id": 105,
                "name": "Demo Offer 5 (E-commerce CPS)",
                "status": "active",
                "state": "active",
                "is_available": True,
                "payout_value": 15.0,
                "payout_currency": "USD",
            },
        }

        self.domains: dict[int, dict[str, Any]] = {
            1: {
                "id": 1,
                "name": "track-demo.example.com",
                "domain": "track-demo.example.com",
                "is_ssl": True,
                "default": True,
            }
        }

        self.groups: dict[int, dict[str, Any]] = {
            1: {
                "id": 1,
                "name": "Demo Campaigns Group",
                "type": "campaigns",
                "position": 1,
            }
        }

        self.traffic_sources: dict[int, dict[str, Any]] = {
            1: {
                "id": 1,
                "name": "Google Ads Demo",
                "parameters": {
                    "source": {"name": "utm_source", "placeholder": "{network}"},
                    "campaign": {"name": "utm_campaign", "placeholder": "{campaignid}"},
                },
            }
        }

        self.campaigns: dict[int, dict[str, Any]] = {
            1: {
                "id": 1,
                "name": f"test_{self.surname}_demo_flow",
                "alias": "demo-flow",
                "state": "active",
                "domain_id": 1,
                "group_id": 1,
                "traffic_source_id": 1,
                "cost_type": "CPC",
                "cost_value": 0.1,
            }
        }

        self.streams: dict[int, dict[str, Any]] = {
            10: {
                "id": 10,
                "campaign_id": 1,
                "name": "Фильтр по странам (Flow 1)",
                "type": "regular",
                "schema": "redirect",
                "action_type": "http",
                "action_options": {"url": "https://google.com"},
                "action_payload": "https://google.com",
                "collect_clicks": True,
                "weight": 100,
                "position": 1,
                "filters": [
                    {
                        "name": "country",
                        "mode": "ACCEPT",
                        "payload": ["KZ", "CA"],
                    }
                ],
                "landings": [],
                "offers": [],
            },
            11: {
                "id": 11,
                "campaign_id": 1,
                "name": "Основной поток (Flow 2)",
                "type": "default",
                "schema": "landings_offers",
                "collect_clicks": True,
                "weight": 100,
                "position": 2,
                "filters": [],
                "landings": [],
                "offers": [
                    {"offer_id": 101, "share": 34, "state": "active"},
                    {"offer_id": 102, "share": 33, "state": "active"},
                    {"offer_id": 103, "share": 33, "state": "active"},
                ],
            },
        }

        self._campaign_id_seq = 2
        self._stream_id_seq = 12

    def reset(self) -> None:
        """Reset storage to original seed state and clear any active faults."""
        with self._lock:
            self.active_fault = None
            self._init_seed()
            self._save_state()

    def get_domains(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(list(self.domains.values()))

    def get_groups(self, group_type: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            result = list(self.groups.values())
            if group_type:
                result = [g for g in result if g.get("type") == group_type]
            return copy.deepcopy(result)

    def get_traffic_sources(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(list(self.traffic_sources.values()))

    def get_offers(self) -> list[dict[str, Any]]:
        with self._lock:
            # Only return offers that are active and available
            return copy.deepcopy(
                [
                    o
                    for o in self.offers.values()
                    if o.get("is_available", True) and o.get("state") != "deleted"
                ]
            )

    def get_offer(self, offer_id: int) -> dict[str, Any] | None:
        with self._lock:
            offer = self.offers.get(offer_id)
            if not offer or not offer.get("is_available", True) or offer.get("state") == "deleted":
                return None
            return copy.deepcopy(offer)

    def get_campaigns(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(list(self.campaigns.values()))

    def get_campaign(self, campaign_id: int) -> dict[str, Any] | None:
        with self._lock:
            camp = self.campaigns.get(campaign_id)
            return copy.deepcopy(camp) if camp else None

    def create_campaign(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            cid = self._campaign_id_seq
            self._campaign_id_seq += 1
            new_camp = copy.deepcopy(payload)
            new_camp["id"] = cid
            if "alias" not in new_camp or not new_camp["alias"]:
                new_camp["alias"] = f"camp-{cid}"
            if "state" not in new_camp:
                new_camp["state"] = "active"
            self.campaigns[cid] = new_camp
            self._save_state()
            return copy.deepcopy(new_camp)

    def get_campaign_streams(self, campaign_id: int) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(
                [s for s in self.streams.values() if s.get("campaign_id") == campaign_id]
            )

    def get_stream(self, stream_id: int) -> dict[str, Any] | None:
        with self._lock:
            stream = self.streams.get(stream_id)
            return copy.deepcopy(stream) if stream else None

    def create_stream(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            sid = self._stream_id_seq
            self._stream_id_seq += 1
            new_stream = copy.deepcopy(payload)
            new_stream["id"] = sid
            if "offers" not in new_stream:
                new_stream["offers"] = []
            if "filters" not in new_stream:
                new_stream["filters"] = []
            if "landings" not in new_stream:
                new_stream["landings"] = []
            self.streams[sid] = new_stream
            self._save_state()
            return copy.deepcopy(new_stream)

    def update_stream(self, stream_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            stream = self.streams.get(stream_id)
            if not stream:
                return None
            # Preserve existing stream metadata while updating provided fields (e.g. offers)
            updated = copy.deepcopy(stream)
            for k, v in payload.items():
                updated[k] = copy.deepcopy(v)
            self.streams[stream_id] = updated
            self._save_state()
            return copy.deepcopy(updated)

    def delete_stream_offer(self, stream_id: int, offer_id: int) -> dict[str, Any] | None:
        """Remove offer from stream and rebalance remaining active offers to sum 100%."""
        with self._lock:
            stream = self.streams.get(stream_id)
            if not stream:
                return None
            current_offers = stream.get("offers") or []
            if not any(o.get("offer_id") == offer_id for o in current_offers):
                return None  # Offer not attached to this stream
            remaining = [
                o
                for o in current_offers
                if o.get("offer_id") != offer_id and o.get("state", "active") == "active"
            ]

            # Rebalance remaining to sum 100%
            new_shares = _calculate_fair_shares(len(remaining))
            for i, o in enumerate(remaining):
                o["share"] = new_shares[i]

            stream["offers"] = remaining
            self._save_state()
            return copy.deepcopy(stream)

    def disable_global_offer(self, offer_id: int, available: bool = False) -> bool:
        """Disable or re-enable a global catalog offer."""
        with self._lock:
            if offer_id not in self.offers:
                return False
            self.offers[offer_id]["is_available"] = available
            self.offers[offer_id]["state"] = "active" if available else "deleted"
            self._save_state()
            return True

    def set_fault(self, fault_type: str, params: dict[str, Any] | None = None) -> None:
        with self._lock:
            self.active_fault = {"type": fault_type, "params": params or {}}

    def clear_fault(self) -> None:
        with self._lock:
            self.active_fault = None


# Global singleton storage instance for the simulator service
simulator_storage = SimulatorStorage()
