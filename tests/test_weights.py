import pytest

from src.domain.weights import OfferWeightState, WeightDistributionError, calculate_weights


def test_single_active_offer():
    offers = [OfferWeightState(offer_id=1, position=1, share=50, is_active=True, is_pinned=False)]
    res = calculate_weights(offers)
    assert len(res) == 1
    assert res[0].share == 100
    assert res[0].is_active is True


def test_two_active_offers_equal():
    offers = [
        OfferWeightState(offer_id=1, position=1, share=10, is_active=True, is_pinned=False),
        OfferWeightState(offer_id=2, position=2, share=20, is_active=True, is_pinned=False),
    ]
    res = calculate_weights(offers)
    assert [o.share for o in res] == [50, 50]
    assert sum(o.share for o in res) == 100


def test_three_active_offers_divmod():
    offers = [
        OfferWeightState(offer_id=1, position=1, share=0, is_active=True, is_pinned=False),
        OfferWeightState(offer_id=2, position=2, share=0, is_active=True, is_pinned=False),
        OfferWeightState(offer_id=3, position=3, share=0, is_active=True, is_pinned=False),
    ]
    res = calculate_weights(offers)
    # 100 // 3 = 33, remainder = 1 -> first gets 34, then 33, 33
    assert [o.share for o in res] == [34, 33, 33]
    assert sum(o.share for o in res) == 100


def test_four_active_offers():
    offers = [
        OfferWeightState(offer_id=1, position=1, share=0, is_active=True, is_pinned=False),
        OfferWeightState(offer_id=2, position=2, share=0, is_active=True, is_pinned=False),
        OfferWeightState(offer_id=3, position=3, share=0, is_active=True, is_pinned=False),
        OfferWeightState(offer_id=4, position=4, share=0, is_active=True, is_pinned=False),
    ]
    res = calculate_weights(offers)
    assert [o.share for o in res] == [25, 25, 25, 25]
    assert sum(o.share for o in res) == 100


def test_pinned_offer_preservation_and_remainder():
    # Offer 1 is pinned at 25%. Offers 2 and 3 are unpinned.
    # Remainder is 75. 75 // 2 = 37, remainder = 1.
    # Offer 2 gets 38%, Offer 3 gets 37%.
    offers = [
        OfferWeightState(offer_id=1, position=1, share=25, is_active=True, is_pinned=True),
        OfferWeightState(offer_id=2, position=2, share=0, is_active=True, is_pinned=False),
        OfferWeightState(offer_id=3, position=3, share=0, is_active=True, is_pinned=False),
    ]
    res = calculate_weights(offers)
    res_dict = {o.offer_id: o.share for o in res}
    assert res_dict[1] == 25
    assert res_dict[2] == 38
    assert res_dict[3] == 37
    assert sum(res_dict.values()) == 100


def test_inactive_removed_offer_is_zero():
    offers = [
        OfferWeightState(offer_id=1, position=1, share=25, is_active=True, is_pinned=True),
        OfferWeightState(offer_id=2, position=2, share=75, is_active=True, is_pinned=False),
        OfferWeightState(offer_id=3, position=3, share=99, is_active=False, is_pinned=False),
    ]
    res = calculate_weights(offers)
    res_dict = {o.offer_id: o.share for o in res}
    assert res_dict[1] == 25
    assert res_dict[2] == 75
    assert res_dict[3] == 0


def test_active_zero_when_pinned_sum_is_100():
    # Pinned is 100%, unpinned gets 0% active
    offers = [
        OfferWeightState(offer_id=1, position=1, share=100, is_active=True, is_pinned=True),
        OfferWeightState(offer_id=2, position=2, share=0, is_active=True, is_pinned=False),
    ]
    res = calculate_weights(offers)
    res_dict = {o.offer_id: o.share for o in res}
    assert res_dict[1] == 100
    assert res_dict[2] == 0
    assert sum(res_dict.values()) == 100


def test_error_pinned_exceeds_100():
    offers = [
        OfferWeightState(offer_id=1, position=1, share=60, is_active=True, is_pinned=True),
        OfferWeightState(offer_id=2, position=2, share=50, is_active=True, is_pinned=True),
    ]
    with pytest.raises(WeightDistributionError, match="exceeds 100%"):
        calculate_weights(offers)


def test_error_all_pinned_sum_less_than_100():
    offers = [
        OfferWeightState(offer_id=1, position=1, share=40, is_active=True, is_pinned=True),
        OfferWeightState(offer_id=2, position=2, share=30, is_active=True, is_pinned=True),
    ]
    with pytest.raises(WeightDistributionError, match="not 100%"):
        calculate_weights(offers)


def test_error_no_active_offers():
    offers = [
        OfferWeightState(offer_id=1, position=1, share=0, is_active=False, is_pinned=False),
    ]
    with pytest.raises(WeightDistributionError, match="at least one active offer"):
        calculate_weights(offers)
