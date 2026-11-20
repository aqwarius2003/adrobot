"""Storage and state management for autonomous Keitaro simulator."""

import copy
import hashlib
import json
import os
import threading
from typing import Any

from src.core.config import settings
from src.domain.campaign_names import (
    CampaignNameConflictError,
    campaign_name_key,
    normalize_campaign_name,
)


def compute_stream_fingerprint(stream: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 fingerprint for a stream configuration."""
    offers = stream.get("offers") or []
    normalized_offers = sorted(
        [
            {
                "offer_id": o.get("offer_id"),
                "share": o.get("share"),
                "state": o.get("state", "active"),
            }
            for o in offers
        ],
        key=lambda x: x["offer_id"] or 0,
    )
    content = {
        "type": stream.get("type"),
        "schema": stream.get("schema"),
        "action_type": stream.get("action_type"),
        "action_payload": stream.get("action_payload"),
        "filters": stream.get("filters") or [],
        "landings": stream.get("landings") or [],
        "offers": normalized_offers,
    }
    encoded = json.dumps(content, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
        self._is_corrupt = False
        if state_file is not None:
            self.state_file = state_file
        elif os.environ.get("SIMULATOR_STATE_FILE"):
            self.state_file = os.environ["SIMULATOR_STATE_FILE"]
        elif "PYTEST_CURRENT_TEST" in os.environ or os.environ.get("ADROBOT_TESTING") == "1":
            self.state_file = None
        else:
            self.state_file = os.path.join(os.getcwd(), "data", "simulator_state.json")

        load_status = self._load_state()
        if load_status == "missing":
            self._init_seed()
            self._save_state()
        elif load_status == "corrupt":
            self._is_corrupt = True
            self._init_seed()
            from src.core.logging import logger

            logger.critical(
                "Simulator state file is corrupt. Running with temporary in-memory seed without overwriting '%s'.",
                self.state_file,
            )

    def _snapshot_state(self) -> dict[str, Any]:
        return {
            "offers": copy.deepcopy(self.offers),
            "domains": copy.deepcopy(self.domains),
            "groups": copy.deepcopy(self.groups),
            "traffic_sources": copy.deepcopy(self.traffic_sources),
            "campaigns": copy.deepcopy(self.campaigns),
            "streams": copy.deepcopy(self.streams),
            "_campaign_id_seq": self._campaign_id_seq,
            "_stream_id_seq": self._stream_id_seq,
        }

    def _restore_snapshot(self, snap: dict[str, Any]) -> None:
        self.offers = snap["offers"]
        self.domains = snap["domains"]
        self.groups = snap["groups"]
        self.traffic_sources = snap["traffic_sources"]
        self.campaigns = snap["campaigns"]
        self.streams = snap["streams"]
        self._campaign_id_seq = snap["_campaign_id_seq"]
        self._stream_id_seq = snap["_stream_id_seq"]

    def _save_state(self) -> None:
        if self._is_corrupt:
            raise RuntimeError(
                "Simulator state file is corrupt. Normal writes are blocked until explicit reset."
            )
        if not self.state_file:
            return
        if os.environ.get("ADROBOT_TESTING") == "1":
            default_path = os.path.normpath(
                os.path.abspath(os.path.join(os.getcwd(), "data", "simulator_state.json"))
            )
            current_path = os.path.normpath(os.path.abspath(self.state_file))
            if current_path == default_path:
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
        except Exception as exc:
            from src.core.logging import logger

            logger.error("Failed to persist simulator state to '%s': %s", self.state_file, exc)
            raise OSError(f"Failed to persist simulator state: {exc}") from exc

    def _load_state(self) -> str:
        """Returns 'loaded' on success, 'missing' if file not found, 'corrupt' if file corrupted."""
        if not self.state_file or not os.path.exists(self.state_file):
            return "missing"
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

            for s in self.streams.values():
                s.setdefault("clicks_today", 0)

            # Recover simulator-only roles from the same configuration Creator sends to KT.
            for c_id in self.campaigns:
                for stream_id, role in self._resolve_stream_roles(c_id).items():
                    self.streams[stream_id]["role"] = role

            return "loaded"
        except Exception as exc:
            import shutil
            import time

            from src.core.logging import logger

            logger.error(
                "Simulator state file '%s' is corrupt or unreadable: %s", self.state_file, exc
            )
            try:
                backup_path = f"{self.state_file}.corrupt.{int(time.time())}"
                shutil.copy2(self.state_file, backup_path)
                logger.warning("Corrupt state preserved at '%s'", backup_path)
            except Exception as backup_exc:
                logger.error("Failed to backup corrupt state file: %s", backup_exc)
            return "corrupt"

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
                "name": f"test_{self.surname}_demo_three_offers",
                "alias": "demo-three-offers",
                "state": "active",
                "domain_id": 1,
                "group_id": 1,
                "traffic_source_id": 1,
                "cost_type": "CPC",
                "cost_value": 0.1,
            },
            2: {
                "id": 2,
                "name": f"test_{self.surname}_demo_five_offers",
                "alias": "demo-five-offers",
                "state": "active",
                "domain_id": 1,
                "group_id": 1,
                "traffic_source_id": 1,
                "cost_type": "CPC",
                "cost_value": 0.1,
            },
            3: {
                "id": 3,
                "name": f"test_{self.surname}_demo_two_offers",
                "alias": "demo-two-offers",
                "state": "active",
                "domain_id": 1,
                "group_id": 1,
                "traffic_source_id": 1,
                "cost_type": "CPC",
                "cost_value": 0.1,
            },
        }

        self.streams: dict[int, dict[str, Any]] = {
            10: {
                "id": 10,
                "campaign_id": 1,
                "name": "Фильтр по странам (Flow 1)",
                "role": "flow_1",
                "type": "regular",
                "schema": "redirect",
                "action_type": "http",
                "action_options": {"url": "https://google.com"},
                "action_payload": "https://google.com",
                "collect_clicks": True,
                "clicks_today": 0,
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
                "role": "flow_2",
                "type": "default",
                "schema": "landings_offers",
                "collect_clicks": True,
                "clicks_today": 0,
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
            12: {
                "id": 12,
                "campaign_id": 2,
                "name": "Фильтр по странам (Flow 1)",
                "role": "flow_1",
                "type": "regular",
                "schema": "redirect",
                "action_type": "http",
                "action_options": {"url": "https://google.com"},
                "action_payload": "https://google.com",
                "collect_clicks": True,
                "clicks_today": 0,
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
            13: {
                "id": 13,
                "campaign_id": 2,
                "name": "Основной поток (Flow 2)",
                "role": "flow_2",
                "type": "default",
                "schema": "landings_offers",
                "collect_clicks": True,
                "clicks_today": 0,
                "weight": 100,
                "position": 2,
                "filters": [],
                "landings": [],
                "offers": [
                    {"offer_id": 101, "share": 20, "state": "active"},
                    {"offer_id": 102, "share": 20, "state": "active"},
                    {"offer_id": 103, "share": 20, "state": "active"},
                    {"offer_id": 104, "share": 20, "state": "active"},
                    {"offer_id": 105, "share": 20, "state": "active"},
                ],
            },
            14: {
                "id": 14,
                "campaign_id": 3,
                "name": "Служебный поток (Flow 1)",
                "role": "flow_1",
                "type": "regular",
                "schema": "redirect",
                "action_type": "http",
                "action_options": {"url": "https://google.com"},
                "action_payload": "https://google.com",
                "collect_clicks": True,
                "clicks_today": 0,
                "weight": 100,
                "position": 1,
                "filters": [],
                "landings": [],
                "offers": [],
            },
            15: {
                "id": 15,
                "campaign_id": 3,
                "name": "Основной поток (Flow 2)",
                "role": "flow_2",
                "type": "default",
                "schema": "landings_offers",
                "collect_clicks": True,
                "clicks_today": 0,
                "weight": 100,
                "position": 2,
                "filters": [],
                "landings": [],
                "offers": [
                    {"offer_id": 101, "share": 50, "state": "active"},
                    {"offer_id": 102, "share": 50, "state": "active"},
                ],
            },
        }

        self._campaign_id_seq = 4
        self._stream_id_seq = 16

    def _resolve_stream_roles(self, campaign_id: int) -> dict[int, str]:
        """Identify an unambiguous Creator pair without trusting editable stream names."""
        streams = [s for s in self.streams.values() if s.get("campaign_id") == campaign_id]
        flow1 = [
            s
            for s in streams
            if s.get("type") == "regular"
            and s.get("schema") == "redirect"
            and s.get("role") in (None, "flow_1")
        ]
        flow2 = [
            s
            for s in streams
            if s.get("type") == "default"
            and s.get("schema") in ("landings", "landings_offers")
            and s.get("role") in (None, "flow_2")
        ]
        if len(flow1) != 1 or len(flow2) != 1:
            return {}
        return {flow1[0]["id"]: "flow_1", flow2[0]["id"]: "flow_2"}

    def update_stream_offers(
        self,
        campaign_id: int,
        stream_id: int,
        offers: list[dict[str, Any]],
        expected_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        """Update stream offers in simulator with fingerprint conflict detection."""
        with self._lock:
            stream = self.streams.get(stream_id)
            if not stream or stream.get("campaign_id") != campaign_id:
                raise KeyError(f"Stream #{stream_id} not found in campaign #{campaign_id}")

            roles = self._resolve_stream_roles(campaign_id)
            if roles.get(stream_id) != "flow_2":
                raise ValueError(
                    "Только основной поток (Flow 2) доступен для редактирования в симуляторе"
                )

            current_fp = compute_stream_fingerprint(stream)
            if expected_fingerprint is not None and expected_fingerprint != current_fp:
                raise ValueError("Conflict: remote stream state has changed (fingerprint mismatch)")

            if not offers:
                raise ValueError("Offers list cannot be empty")

            active_offers = [o for o in offers if o.get("state", "active") == "active"]
            if not active_offers:
                raise ValueError("At least one active offer is required")

            total_share = sum(o.get("share", 0) for o in active_offers)
            if total_share != 100:
                raise ValueError(f"Sum of active shares must be 100% (currently {total_share}%)")

            # Check that all offer_ids exist in catalog
            for off in offers:
                oid = off.get("offer_id")
                if oid not in self.offers:
                    raise ValueError(f"Offer #{oid} does not exist in simulator catalog")

            # Update stream offers with rollback protection on disk failure
            snap = self._snapshot_state()
            try:
                for role_stream_id, role in roles.items():
                    self.streams[role_stream_id]["role"] = role
                self.streams[stream_id]["offers"] = copy.deepcopy(offers)
                self._save_state()
                result = copy.deepcopy(self.streams[stream_id])
                result["fingerprint"] = compute_stream_fingerprint(result)
                return result
            except Exception:
                self._restore_snapshot(snap)
                raise

    def reset(self) -> None:
        """Reset storage to original seed state and clear any active faults."""
        with self._lock:
            snap = self._snapshot_state()
            try:
                self.active_fault = None
                self._init_seed()
                self._is_corrupt = False
                self._save_state()
            except Exception:
                self._restore_snapshot(snap)
                raise

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
            name = normalize_campaign_name(payload.get("name"))
            if any(
                campaign_name_key(c.get("name")) == campaign_name_key(name)
                for c in self.campaigns.values()
            ):
                raise CampaignNameConflictError()
            snap = self._snapshot_state()
            try:
                cid = self._campaign_id_seq
                self._campaign_id_seq += 1
                new_camp = copy.deepcopy(payload)
                new_camp["name"] = name
                new_camp["id"] = cid
                if "alias" not in new_camp or not new_camp["alias"]:
                    new_camp["alias"] = f"camp-{cid}"
                if "state" not in new_camp:
                    new_camp["state"] = "active"
                self.campaigns[cid] = new_camp
                self._save_state()
                return copy.deepcopy(new_camp)
            except Exception:
                self._restore_snapshot(snap)
                raise

    def get_campaign_streams(self, campaign_id: int) -> list[dict[str, Any]]:
        with self._lock:
            result = []
            for s in self.streams.values():
                if s.get("campaign_id") == campaign_id:
                    item = copy.deepcopy(s)
                    item["fingerprint"] = compute_stream_fingerprint(s)
                    result.append(item)
            return result

    def get_stream(self, stream_id: int) -> dict[str, Any] | None:
        with self._lock:
            stream = self.streams.get(stream_id)
            if not stream:
                return None
            res = copy.deepcopy(stream)
            res["fingerprint"] = compute_stream_fingerprint(stream)
            return res

    def create_stream(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            snap = self._snapshot_state()
            try:
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
                for role_stream_id, role in self._resolve_stream_roles(
                    new_stream.get("campaign_id")
                ).items():
                    self.streams[role_stream_id]["role"] = role
                self._save_state()
                return copy.deepcopy(new_stream)
            except Exception:
                self._restore_snapshot(snap)
                raise

    def update_stream(self, stream_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            stream = self.streams.get(stream_id)
            if not stream:
                return None
            snap = self._snapshot_state()
            try:
                updated = copy.deepcopy(stream)
                for k, v in payload.items():
                    updated[k] = copy.deepcopy(v)
                self.streams[stream_id] = updated
                self._save_state()
                return copy.deepcopy(updated)
            except Exception:
                self._restore_snapshot(snap)
                raise

    def delete_stream_offer(self, stream_id: int, offer_id: int) -> dict[str, Any] | None:
        """Remove offer from stream and rebalance remaining active offers to sum 100%."""
        with self._lock:
            stream = self.streams.get(stream_id)
            if not stream:
                return None
            current_offers = stream.get("offers") or []
            if not any(o.get("offer_id") == offer_id for o in current_offers):
                return None  # Offer not attached to this stream

            snap = self._snapshot_state()
            try:
                # Deepcopy remaining offers before modifying shares to prevent in-place mutation of memory
                remaining = [
                    copy.deepcopy(o)
                    for o in current_offers
                    if o.get("offer_id") != offer_id and o.get("state", "active") == "active"
                ]

                # Rebalance remaining to sum 100%
                new_shares = _calculate_fair_shares(len(remaining))
                for i, o in enumerate(remaining):
                    o["share"] = new_shares[i]

                self.streams[stream_id]["offers"] = remaining
                self._save_state()
                return copy.deepcopy(self.streams[stream_id])
            except Exception:
                self._restore_snapshot(snap)
                raise

    def disable_global_offer(self, offer_id: int, available: bool = False) -> bool:
        """Disable or re-enable a global catalog offer."""
        with self._lock:
            if offer_id not in self.offers:
                return False
            snap = self._snapshot_state()
            try:
                self.offers[offer_id]["is_available"] = available
                self.offers[offer_id]["state"] = "active" if available else "deleted"
                self._save_state()
                return True
            except Exception:
                self._restore_snapshot(snap)
                raise

    def set_fault(self, fault_type: str, params: dict[str, Any] | None = None) -> None:
        with self._lock:
            self.active_fault = {"type": fault_type, "params": params or {}}

    def clear_fault(self) -> None:
        with self._lock:
            self.active_fault = None


# Global singleton storage instance for the simulator service
simulator_storage = SimulatorStorage()
