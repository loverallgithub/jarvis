"""Tier pricing — DEC-001.

Money bugs are the quietest bugs in the system: the API accepts the number, the
page renders it, and nothing complains. Pimlico listed six products at 100×
their intended price and it was found only by reading the value back from the
provider — after they were live.
"""
from __future__ import annotations

import pytest

from jarvis.commerce import pricing
from jarvis.commerce.pricing import PricingError


async def test_ladder_follows_the_decided_ratios(clean_db):
    """DEC-001: 1x / 3-4x / 10-15x, midpoint of each band."""
    rungs = await pricing.ladder(29700)          # €297 anchor
    by_tier = {r.tier: r for r in rungs}

    assert by_tier["roadmap"].price_minor == 29700
    assert by_tier["instructions"].price_minor == 104000    # 3.5x, snapped to €1
    assert by_tier["deployed"].price_minor == 371300        # 12.5x, snapped to €1


async def test_the_ladder_is_strictly_increasing(clean_db):
    rungs = await pricing.ladder(10000)
    prices = [r.price_minor for r in rungs]
    assert prices == sorted(prices)
    assert len(set(prices)) == 3


async def test_a_non_increasing_policy_is_refused(clean_db):
    """An Instructions tier at or below Roadmap makes the upgrade delta
    negative and the ladder meaningless."""
    await clean_db.execute(
        "UPDATE pricing_policy SET ratio_min=0.5, ratio_max=0.5 WHERE tier='instructions'")
    with pytest.raises(PricingError, match="not strictly increasing"):
        await pricing.ladder(29700)


async def test_a_float_anchor_is_refused(clean_db):
    """🔴 The 100x bug's entry point. A float here means someone is thinking in
    euros, and the next line will multiply or divide by 100 one time too many."""
    with pytest.raises(PricingError, match="MINOR units"):
        await pricing.ladder(297.00)                 # type: ignore[arg-type]


async def test_a_bool_is_not_an_int(clean_db):
    """bool is a subclass of int in Python; True would otherwise price at €0.01."""
    with pytest.raises(PricingError):
        await pricing.ladder(True)                   # type: ignore[arg-type]


async def test_a_non_positive_anchor_is_refused(clean_db):
    for bad in (0, -100):
        with pytest.raises(PricingError):
            await pricing.ladder(bad)


async def test_ratios_are_data_so_a_retune_needs_no_deploy(clean_db):
    before = {r.tier: r.price_minor for r in await pricing.ladder(29700)}
    await clean_db.execute(
        "UPDATE pricing_policy SET ratio_min=5, ratio_max=5 WHERE tier='instructions'")
    after = {r.tier: r.price_minor for r in await pricing.ladder(29700)}
    assert after["instructions"] == 148500
    assert after["instructions"] != before["instructions"]
    assert after["roadmap"] == before["roadmap"]


async def test_an_incomplete_policy_refuses_to_guess(clean_db):
    await clean_db.execute("DELETE FROM pricing_policy WHERE tier='deployed'")
    with pytest.raises(PricingError, match="refusing to guess"):
        await pricing.ladder(29700)


def test_each_tier_is_a_superset_of_the_one_below():
    assert pricing.tiers_covered("roadmap") == ["roadmap"]
    assert pricing.tiers_covered("instructions") == ["roadmap", "instructions"]
    assert pricing.tiers_covered("deployed") == ["roadmap", "instructions", "deployed"]


def test_delta_tiers_are_only_what_the_upgrade_adds():
    assert pricing.delta_tiers("roadmap", "instructions") == ["instructions"]
    assert pricing.delta_tiers("roadmap", "deployed") == ["instructions", "deployed"]
    assert pricing.delta_tiers("instructions", "deployed") == ["deployed"]


def test_a_sideways_or_downward_move_is_not_an_upgrade():
    assert pricing.is_upgrade("roadmap", "deployed") is True
    assert pricing.is_upgrade("deployed", "roadmap") is False
    assert pricing.is_upgrade("roadmap", "roadmap") is False
    with pytest.raises(PricingError, match="not an upgrade"):
        pricing.delta_tiers("deployed", "roadmap")


def test_a_free_upgrade_is_a_bug_not_generosity():
    assert pricing.upgrade_delta_minor(29700, 104000) == 74300
    for a, b in ((29700, 29700), (104000, 29700)):
        with pytest.raises(PricingError, match="must be positive"):
            pricing.upgrade_delta_minor(a, b)


def test_major_units_are_for_display_only():
    tp = pricing.TierPrice("roadmap", 29700, __import__("decimal").Decimal(1))
    assert tp.as_major == "297.00"
    assert isinstance(tp.price_minor, int)
