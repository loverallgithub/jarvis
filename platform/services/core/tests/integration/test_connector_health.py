"""The dormancy state machine (C3).

Pimlico's `dormant` was a hand-set registry flag. Nobody ever set it, so
`google_trends`, `indie_hackers` and `app_store_reviews` returned 0 items
every day for weeks with `dormant: []` and the funnel quietly starved.

⚠️ A correction worth keeping: those three sources are **not dead** — called
directly they return 1, 2 and 29 signals. The defect was never "the sources
broke", it was that the platform had no way to *notice* either state. Health
here is COMPUTED from observed behaviour, so a silently-dead source flags
itself and a recovering one is not written off.
"""
from __future__ import annotations

import pytest

from jarvis import db
from jarvis.connectors import base as connectors


async def test_everything_starts_dormant(clean_db):
    """A connector that has never passed a contract test cannot emit."""
    assert await connectors.state_of("tubeonai") == "dormant"


async def test_an_unregistered_connector_is_dormant_not_live(clean_db):
    """Absent must be the safe direction."""
    assert await connectors.state_of("nonexistent") == "dormant"


async def test_only_a_contract_test_grants_live(clean_db):
    """Reachable is not the same as parseable.

    A service can be up, authenticated and returning 200 while having renamed
    the field we depend on — which produces plausible zeros, not errors. So a
    passing probe deliberately does NOT restore `live`.
    """
    await connectors.record_probe("reddit", True, "200 ok")
    assert await connectors.state_of("reddit") == "dormant"

    await connectors.record_contract_test("reddit", True, "shape ok")
    assert await connectors.state_of("reddit") == "live"


async def test_two_probe_failures_degrade_four_go_dormant(clean_db):
    await connectors.record_contract_test("reddit", True, "shape ok")
    assert await connectors.state_of("reddit") == "live"

    await connectors.record_probe("reddit", False, "timeout")
    assert await connectors.state_of("reddit") == "live", "one blip is not a failure"

    await connectors.record_probe("reddit", False, "timeout")
    assert await connectors.state_of("reddit") == "degraded"

    await connectors.record_probe("reddit", False, "timeout")
    await connectors.record_probe("reddit", False, "timeout")
    assert await connectors.state_of("reddit") == "dormant"


async def test_a_failing_contract_test_goes_straight_to_dormant(clean_db):
    await connectors.record_contract_test("reddit", True)
    await connectors.record_contract_test("reddit", False, "field 'body' is gone")
    assert await connectors.state_of("reddit") == "dormant"


async def test_zero_yield_walks_a_source_toward_dormant(clean_db):
    """THE correction to Pimlico. Zero-yield is a failure signal."""
    await connectors.record_contract_test("google_trends", True)
    assert await connectors.state_of("google_trends") == "live"

    for _ in range(2):
        await connectors.record_yield("google_trends", 0)
    assert await connectors.state_of("google_trends") == "live"

    await connectors.record_yield("google_trends", 0)
    assert await connectors.state_of("google_trends") == "degraded"

    for _ in range(2):
        await connectors.record_yield("google_trends", 0)
    assert await connectors.state_of("google_trends") == "dormant"


async def test_a_single_real_yield_resets_the_streak(clean_db):
    """The other half of the correction: a source that recovers must not stay
    condemned. `app_store_reviews` returns 29 signals on demand."""
    await connectors.record_contract_test("app_store_reviews", True)
    for _ in range(4):
        await connectors.record_yield("app_store_reviews", 0)
    assert await connectors.state_of("app_store_reviews") == "degraded"

    await connectors.record_yield("app_store_reviews", 29)
    row = await db.fetchrow(
        "SELECT zero_yield_streak FROM connector_health WHERE connector='app_store_reviews'")
    assert row["zero_yield_streak"] == 0
    # State stays degraded until a contract test re-proves the shape — recovery
    # is earned, not assumed.
    assert await connectors.state_of("app_store_reviews") == "degraded"
    await connectors.record_contract_test("app_store_reviews", True)
    assert await connectors.state_of("app_store_reviews") == "live"


async def test_quarantine_writes_to_dead_letter(clean_db):
    """Nothing in dead_letter can reach a publish path.

    This is where the string `"[Automation failed: Page.goto: Timeout 30000ms
    exceeded...]"` terminates instead of being posted to LinkedIn.
    """
    poison = "[Automation failed: Page.goto: Timeout 30000ms exceeded]"
    dl_id = await connectors.quarantine("sintra", poison, "failure marker present")

    row = await db.fetchrow("SELECT * FROM dead_letter WHERE id = $1", dl_id)
    assert row["connector"] == "sintra"
    assert row["payload_raw"] == poison
    assert "failure marker" in row["reason"]


async def test_seeded_connectors_are_all_registered(clean_db):
    """The registry must know about every connector the design names, so that
    a step declaring one gets a real dormancy answer rather than a KeyError."""
    snap = {c["connector"] for c in await connectors.snapshot()}
    for expected in ("sintra", "tubeonai", "telegram", "ghl", "stripe",
                     "youtube_data_v3", "skool", "you_com", "databar"):
        assert expected in snap, f"{expected} missing from connector_health"
