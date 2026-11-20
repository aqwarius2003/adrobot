import hashlib
import json
from dataclasses import dataclass
from typing import Any


class WeightDistributionError(ValueError):
    """Raised when weight redistribution fails validation or invariants."""

    pass


@dataclass
class OfferWeightState:
    offer_id: int
    position: int
    share: int
    is_active: bool
    is_pinned: bool


def calculate_weights(offers: list[OfferWeightState]) -> list[OfferWeightState]:
    """
    Pure domain function for recalculating offer weights according to SPEC §11.

    Invariants:
    1. Active offers must sum strictly to 100%.
    2. Removed / inactive offers have share = 0% and do not participate in distribution.
    3. Pinned weights of active offers are preserved.
    4. Remainder R = 100 - sum(P) is distributed among active unpinned U via divmod(R, len(U)).
       The extra +1s are assigned to the first r unpinned offers in stable position order.
    5. Cannot have 0 active offers (cannot remove last active offer).
    6. If all active offers are pinned, their sum must be strictly 100%.
    7. Pinned sum > 100% is rejected.
    """
    active_offers = [o for o in offers if o.is_active]
    inactive_offers = [o for o in offers if not o.is_active]

    if not active_offers:
        raise WeightDistributionError("Stream must have at least one active offer")

    # Inactive / removed offers always have share = 0
    result_inactive = [
        OfferWeightState(
            offer_id=o.offer_id,
            position=o.position,
            share=0,
            is_active=False,
            is_pinned=False,  # Removed offers cannot be pinned
        )
        for o in inactive_offers
    ]

    pinned = [o for o in active_offers if o.is_pinned]
    unpinned = [o for o in active_offers if not o.is_pinned]

    sum_pinned = sum(o.share for o in pinned)

    if any(o.share < 0 or o.share > 100 for o in pinned):
        raise WeightDistributionError("Pinned weights must be between 0 and 100")

    if sum_pinned > 100:
        raise WeightDistributionError(f"Sum of pinned weights ({sum_pinned}%) exceeds 100%")

    remainder = 100 - sum_pinned

    # Stable sort unpinned by position, then offer_id
    sorted_unpinned = sorted(unpinned, key=lambda x: (x.position, x.offer_id))

    if not sorted_unpinned:
        if sum_pinned != 100:
            raise WeightDistributionError(
                f"All active offers are pinned, but their sum is {sum_pinned}%, not 100%"
            )
        result_active = pinned
    else:
        q, r = divmod(remainder, len(sorted_unpinned))
        new_unpinned = []
        for idx, o in enumerate(sorted_unpinned):
            extra = 1 if idx < r else 0
            new_share = q + extra
            new_unpinned.append(
                OfferWeightState(
                    offer_id=o.offer_id,
                    position=o.position,
                    share=new_share,
                    is_active=True,
                    is_pinned=False,
                )
            )
        result_active = pinned + new_unpinned

    # Verify invariants
    total_active_share = sum(o.share for o in result_active)
    if total_active_share != 100:
        raise WeightDistributionError(
            f"Internal calculation error: sum of shares is {total_active_share}% (expected 100%)"
        )

    for o in result_active:
        if o.share < 0 or o.share > 100:
            raise WeightDistributionError(f"Offer {o.offer_id} has invalid share: {o.share}%")

    # Return all in original or stable position order
    all_results = sorted(result_active + result_inactive, key=lambda x: (x.position, x.offer_id))
    return all_results


def compute_stream_fingerprint(stream_data: dict[str, Any]) -> str:
    """Create deterministic SHA256 hash of stream offers and essential routing fields."""
    offers = stream_data.get("offers") or []
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
        "type": stream_data.get("type"),
        "schema": stream_data.get("schema"),
        "action_type": stream_data.get("action_type"),
        "action_payload": stream_data.get("action_payload"),
        "filters": stream_data.get("filters") or [],
        "landings": stream_data.get("landings") or [],
        "offers": normalized_offers,
    }
    encoded = json.dumps(content, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
