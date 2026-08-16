"""THE PHASE-3 EXIT CRITERION.

    "A deliberately-broken connector goes dormant within one interval."

`test_a_deliberately_broken_connector_goes_dormant_in_one_sweep` is that
criterion, executable. The rest of this file protects the surrounding
invariants — the ones that make the state machine trustworthy rather than
merely present.
"""
from __future__ import annotations

import pytest

from jarvis import db
from jarvis.connectors import base, health, registry
from jarvis.connectors.base import ConnectorError, ProbeResult, TestResult
from jarvis.connectors.types import Author, HarvestResult, Signal


class _Fake:
    """A connector we can break on demand."""
    kind = "api"
    source_type = "community"

    def __init__(self, name: str, *, probe_ok=True, contract_ok=True, yield_n=3):
        self.name = name
        self.probe_ok = probe_ok
        self.contract_ok = contract_ok
        self.yield_n = yield_n
        self.calls = 0

    async def probe(self) -> ProbeResult:
        return ProbeResult(ok=self.probe_ok, detail="fake probe")

    async def contract_test(self) -> TestResult:
        return TestResult(ok=self.contract_ok, detail="fake contract")

    async def call(self, limit: int = 25, **kw) -> HarvestResult:
        self.calls += 1
        if not self.contract_ok:
            raise ConnectorError("fake connector is broken")
        return HarvestResult(self.name, [
            Signal(external_id=f"{self.name}-{i}",
                   concept=f"this is a genuine pain statement number {i}",
                   source_type=self.source_type,
                   author=Author(handle=f"user{i}", platform="hackernews"))
            for i in range(self.yield_n)])


@pytest.fixture
def fake(monkeypatch):
    made: dict[str, _Fake] = {}

    def install(name: str, **kw) -> _Fake:
        f = _Fake(name, **kw)
        made[name] = f
        return f

    def _get(name):
        if name in made:
            return made[name]
        raise base.ConnectorError(f"no implementation for {name!r}")

    monkeypatch.setattr(registry, "get", _get)
    monkeypatch.setattr(registry, "implemented", lambda: sorted(made))
    return install


# ===========================================================================
# THE EXIT CRITERION
# ===========================================================================

async def test_a_deliberately_broken_connector_goes_dormant_in_one_sweep(clean_db, fake):
    """Break the SHAPE, not the reachability.

    A contract failure means the service answered but no longer returns what we
    parse — which is the failure mode that produces plausible zeros instead of
    errors, and the one Pimlico could not see. It goes dormant immediately: one
    sweep, not four.
    """
    f = fake("breakable")
    await base.register("breakable", "api", "test")
    await base.record_contract_test("breakable", True, "setup")
    assert await base.state_of("breakable") == "live"

    # …now deliberately break it.
    f.contract_ok = False

    results = await health.check_all(["breakable"])

    assert results[0].state == "dormant"
    assert await base.state_of("breakable") == "dormant", \
        "a broken connector must reach dormant within ONE interval"


async def test_a_dormant_connector_is_not_called_at_all(clean_db, fake):
    """🔴 A dormant connector returning zero is NOT evidence about the source —
    it is evidence we did not ask. Conflating those makes a dormant source look
    like a dead one, and makes the zero-yield counter lie."""
    f = fake("dormant_one")
    await base.register("dormant_one", "api", "test")
    assert await base.state_of("dormant_one") == "dormant"

    result = await health.harvest("dormant_one")

    assert result.count == 0
    assert "not called" in result.detail
    assert f.calls == 0, "the connector body must not have run"
    assert await db.fetchval(
        "SELECT zero_yield_streak FROM connector_health WHERE connector='dormant_one'") == 0, \
        "not asking must not count as a zero yield"


async def test_recovery_requires_a_passing_contract_test(clean_db, fake):
    """Reachable is not parseable. A probe recovering does NOT restore live."""
    f = fake("recovers", contract_ok=False)
    await base.register("recovers", "api", "test")
    await base.record_contract_test("recovers", True, "setup")

    await health.check_all(["recovers"])
    assert await base.state_of("recovers") == "dormant"

    # The service comes back but still returns the wrong shape.
    await base.record_probe("recovers", True, "reachable again")
    assert await base.state_of("recovers") == "dormant"

    f.contract_ok = True
    await health.check_all(["recovers"])
    assert await base.state_of("recovers") == "live"


async def test_an_unreachable_connector_degrades_then_goes_dormant(clean_db, fake):
    """Reachability failures are graded — one blip is not an outage."""
    f = fake("flaky", probe_ok=False)
    await base.register("flaky", "api", "test")
    await base.record_contract_test("flaky", True, "setup")

    await health.check_all(["flaky"])
    assert await base.state_of("flaky") == "live", "one failure is not enough"
    await health.check_all(["flaky"])
    assert await base.state_of("flaky") == "degraded"
    await health.check_all(["flaky"])
    await health.check_all(["flaky"])
    assert await base.state_of("flaky") == "dormant"


async def test_a_connector_that_raises_during_its_own_check_is_treated_as_broken(clean_db,
                                                                                monkeypatch):
    """Never let one bad connector abort the sweep — and never let it look
    healthy because it failed in an unexpected way."""
    class Explodes:
        name = "explodes"
        kind = "api"

        async def probe(self):
            raise RuntimeError("kaboom")

        async def contract_test(self):
            raise RuntimeError("kaboom")

    monkeypatch.setattr(registry, "get", lambda n: Explodes())
    monkeypatch.setattr(registry, "implemented", lambda: ["explodes"])
    await base.register("explodes", "api", "test")
    await base.record_contract_test("explodes", True, "setup")

    results = await health.check_all()
    assert results[0].state == "dormant"


# ===========================================================================
# zero yield — the Pimlico correction
# ===========================================================================

async def test_zero_yield_walks_a_live_connector_to_dormant(clean_db, fake):
    """Pimlico's google_trends, indie_hackers and app_store_reviews returned 0
    items every day for weeks with `dormant: []`."""
    f = fake("empty", yield_n=0)
    await base.register("empty", "api", "test")
    await base.record_contract_test("empty", True, "setup")

    for _ in range(3):
        await health.harvest("empty")
    assert await base.state_of("empty") == "degraded"

    for _ in range(2):
        await health.harvest("empty")
    assert await base.state_of("empty") == "dormant"
    assert f.calls == 5, "a live connector must actually be called each time"


async def test_a_real_yield_resets_the_streak(clean_db, fake):
    """⚠️ The correction to my own earlier claim: Pimlico's 'dead' sources were
    NOT dead — called directly they returned 1, 2 and 29 signals. A source that
    recovers must not stay condemned."""
    f = fake("recovering", yield_n=0)
    await base.register("recovering", "api", "test")
    await base.record_contract_test("recovering", True, "setup")

    for _ in range(3):
        await health.harvest("recovering")
    assert await base.state_of("recovering") == "degraded"

    f.yield_n = 5
    await health.harvest("recovering")
    assert await db.fetchval(
        "SELECT zero_yield_streak FROM connector_health WHERE connector='recovering'") == 0


# ===========================================================================
# harvesting and storage
# ===========================================================================

async def test_harvested_signals_are_stored_and_deduplicated(clean_db, fake):
    fake("hacker_news", yield_n=4)
    await base.register("hacker_news", "api", "test")
    await base.record_contract_test("hacker_news", True, "setup")

    await health.harvest("hacker_news")
    first = await db.fetchval("SELECT count(*) FROM signals")
    assert first == 4

    await health.harvest("hacker_news")
    assert await db.fetchval("SELECT count(*) FROM signals") == first, \
        "the same external_ids must not be stored twice"


async def test_the_author_is_captured_as_a_voice(clean_db, fake):
    """DEC-004 / step A1d. The author is already in every payload we parse;
    Pimlico parsed it and threw it away."""
    fake("hacker_news", yield_n=3)
    await base.register("hacker_news", "api", "test")
    await base.record_contract_test("hacker_news", True, "setup")

    await health.harvest("hacker_news")

    assert await db.fetchval("SELECT count(*) FROM voices") == 3
    assert await db.fetchval("SELECT count(*) FROM voice_mentions") == 3


async def test_community_voices_default_to_do_not_contact(clean_db, fake):
    """🔴 Non-negotiable. These are evidence, never a mailing list."""
    fake("hacker_news", yield_n=2)
    await base.register("hacker_news", "api", "test")
    await base.record_contract_test("hacker_news", True, "setup")
    await health.harvest("hacker_news")

    rows = await db.fetch("SELECT do_not_contact, contactable FROM voices")
    assert rows and all(r["do_not_contact"] is True for r in rows)
    assert all(r["contactable"] is False for r in rows)


async def test_inadmissible_signals_are_counted_but_not_stored(clean_db, monkeypatch):
    """The 4-content-word admission rule. Bare brand names embed as mutually
    similar and formed a 20-member false cluster in Pimlico that would have
    cleared every gate and auto-built garbage."""
    class Terse:
        name = "hacker_news"
        kind = "api"

        async def probe(self):
            return ProbeResult(ok=True)

        async def contract_test(self):
            return TestResult(ok=True)

        async def call(self, limit=25, **kw):
            return HarvestResult(self.name, [
                Signal(external_id="a", concept="Slack", source_type="community"),
                Signal(external_id="b", concept="Zoom bad", source_type="community"),
                Signal(external_id="c",
                       concept="reconciling invoices by hand every month",
                       source_type="community")])

    monkeypatch.setattr(registry, "get", lambda n: Terse())
    await base.register("hacker_news", "api", "test")
    await base.record_contract_test("hacker_news", True, "setup")

    r = await health.harvest("hacker_news")
    assert r.count == 3
    assert len(r.admissible) == 1
    assert await db.fetchval("SELECT count(*) FROM signals") == 1


async def test_registry_orphans_are_reported_in_both_directions(clean_db):
    """A row with no implementation can never emit — and would sit at zero
    yield forever without anyone noticing."""
    o = await registry.orphans()
    assert "reddit" not in o["rows_without_code"], "reddit HAS an implementation"
    # `skool` is browser-only and `tubeonai` has no published endpoint contract
    # (DEC-003), so both are seeded as rows that nothing can harvest yet.
    assert "skool" in o["rows_without_code"]
    assert "tubeonai" in o["rows_without_code"]
    # The six authority channels WERE in this list. HT-002 gave them one
    # implementation each, so a regression that unregisters them is a test
    # failure rather than six sources quietly returning nothing.
    assert "yt_alex_hormozi" not in o["rows_without_code"]
    # Credential-health connectors have no `sources` row by design — that is
    # the other direction of drift, and it is expected, not a defect.
    assert "youtube_data_v3" in o["code_without_rows"]


async def test_indie_hackers_reports_a_removed_feed_not_a_parse_bug(clean_db):
    """The feed was REMOVED, not moved — verified from this VPS 2026-08-08.

    It previously failed with *"feed is not valid XML: line 1, column 26"*,
    which reads like a transient markup change and sends a session hunting a
    new URL. There is none: ten candidate paths all return 200 with HTML, and
    the SPA serves a byte-identical body for a nonsense path, so the 200 is not
    evidence of anything. The error must say that.
    """
    r = registry.get("indie_hackers")
    with pytest.raises(ConnectorError, match="no longer publishes a feed"):
        await r.call()

    # It must stay REGISTERED. Deleting the class would drop the seeded
    # `sources` row into rows_without_code, which is the report for "nobody
    # built this yet" — a different problem needing a different fix.
    assert "indie_hackers" not in (await registry.orphans())["rows_without_code"]


async def test_product_hunt_still_parses_so_the_rss_base_is_not_at_fault(clean_db):
    """Rules out our parser. Both sat on `RssSource`; only one host changed."""
    assert registry.get("product_hunt").feed_url.startswith("https://www.producthunt.com")
    assert not hasattr(registry.get("indie_hackers"), "feed_url"), \
        "indie_hackers must no longer advertise a feed URL it cannot serve"


async def test_reddit_is_implemented_but_reports_why_it_is_blocked(clean_db):
    """403 from this VPS, verified 2026-08-07. Kept as a real class so the state
    machine says 'dormant because blocked' rather than the source silently not
    existing — a missing connector and a blocked one need different fixes."""
    r = registry.get("reddit")
    with pytest.raises(ConnectorError, match="403"):
        await r.call()
