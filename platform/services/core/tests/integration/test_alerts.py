"""Alert rules and their synthetic tests — C2.

Pimlico's roadmap correctly named "nothing reports failure" as the root cause of
every other defect, built eleven alert rules to fix it, and **four of those
detectors are silently broken today**. The lesson was never "add more rules".

So this file tests the *detectors*, not the features. If one of these fails, an
alert we believe in has stopped working.
"""
from __future__ import annotations

import pytest

from jarvis import db
from jarvis.observability import alerts as al


async def test_every_synthetic_actually_fires(clean_db):
    """🔴 THE C2 TEST. Each rule with a synthetic creates its own condition and
    the predicate must return True. A synthetic that does not fire means the
    detector would not have caught the thing it exists for."""
    out = await al.run_synthetics()

    failures = {k: v for k, v in out["results"].items()
                if v in ("did_not_fire", "error")}
    assert not failures, f"these detectors did not fire: {failures}"
    assert out["fired"] == len(al.RULES), "every rule now has a synthetic"


async def test_synthetics_clean_up_after_themselves(clean_db):
    """A synthetic that leaves rows behind would trip the real alert it just
    tested — and then look like a genuine incident."""
    await al.run_synthetics()

    from jarvis.commerce.fulfilment import undelivered_paid_orders
    from jarvis.commerce.notify import pending_and_failed

    assert await undelivered_paid_orders() == []
    assert (await pending_and_failed()).get("owed", 0) == 0
    assert await db.fetchval(
        "SELECT count(*) FROM artifacts WHERE missing_since IS NOT NULL") == 0
    assert await db.fetchval(
        "SELECT count(*) FROM connector_health WHERE connector LIKE '\\_\\_synthetic%'") == 0
    assert await db.fetchval(
        "SELECT count(*) FROM human_tasks WHERE ref = '__SYN__'") == 0
    assert await db.fetchval("SELECT count(*) FROM orders") == 0
    assert await db.fetchval("SELECT count(*) FROM needs") == 0


async def test_results_are_recorded_for_the_meta_alert(clean_db):
    await al.run_synthetics()
    rows = await db.fetch("SELECT alert_name, last_result FROM alert_synthetics")
    assert len(rows) == len(al.RULES)
    by_name = {r["alert_name"]: r["last_result"] for r in rows}
    assert by_name["UndeliveredPaidOrders"] == "fired"
    assert by_name["ConnectorRegressed"] == "fired"
    # ServiceDown's synthetic covers the delivery path (console webhook +
    # stream config), not the outage itself — see the rule's rationale.
    assert by_name["ServiceDown"] == "fired"


async def test_stale_synthetics_feed_alert_never_tripped(clean_db):
    await al.run_synthetics()
    assert not [s for s in await al.stale_synthetics()
                if s["alert_name"] == "UndeliveredPaidOrders"]

    await db.execute(
        "UPDATE alert_synthetics SET last_tripped_at = now() - interval '20 days' "
        "WHERE alert_name = 'UndeliveredPaidOrders'")
    stale = {s["alert_name"] for s in await al.stale_synthetics()}
    assert "UndeliveredPaidOrders" in stale


async def test_a_never_run_rule_is_stale_immediately(clean_db):
    """'Never verified' must be WORSE than 'verified 11 days ago', not better.
    A NULL timestamp sorting as fresh is how an unverified detector hides.

    No shipped rule lacks a synthetic any more, so the behaviour is pinned
    with a temporary rule appended to RULES for the duration of the test."""
    fake = al.Rule("__SyntheticlessRule__", "vector(0)", "1m", "info",
                   "test-only rule with no synthetic", "pins never_run staleness",
                   None)
    al.RULES.append(fake)
    try:
        await al.run_synthetics()
        stale = {s["alert_name"] for s in await al.stale_synthetics()}
        assert "__SyntheticlessRule__" in stale, \
            "a rule with no synthetic must show as stale"
    finally:
        al.RULES.remove(fake)
        await db.execute(
            "DELETE FROM alert_synthetics WHERE alert_name = '__SyntheticlessRule__'")


# ---------------------------------------------------------------------------
# the rule definitions themselves
# ---------------------------------------------------------------------------

def test_every_freshness_rule_guards_against_a_zero_gauge():
    """🔴 A gauge at 0 means UNKNOWN, not stale.

    Pimlico had an alert on a Gauge defaulting to 0, so every restart
    false-alarmed until people learned to ignore it — which is how an alert
    stops being an alert.
    """
    for r in al.RULES:
        if "last_success_timestamp" in r.expr:
            assert "> 0" in r.expr, \
                f"{r.name} uses a timestamp gauge without a `> 0` guard"


def test_freshness_thresholds_are_derived_from_the_declared_interval():
    """Pimlico's NoSuccessfulScan was hardcoded at 10 days for a weekly job, so
    it needed two consecutive misses before it could fire."""
    sweep = al.by_name("ConnectorSweepStale")
    assert "1350" in sweep.expr          # 900 * 1.5
    harvest = al.by_name("HarvestStale")
    assert "129600" in harvest.expr      # 86400 * 1.5


def test_every_rule_states_why_it_exists():
    """Whoever is paged at 3am needs to know what they are looking at, and the
    rationale is rendered into the rule file itself."""
    for r in al.RULES:
        assert len(r.rationale) > 40, f"{r.name} has no meaningful rationale"
        assert r.severity in ("critical", "warning", "info")


def test_the_meta_alert_exists_and_matches_its_threshold():
    r = al.by_name("AlertNeverTripped")
    assert str(al.SYNTHETIC_MAX_AGE_DAYS) in r.expr


def test_the_money_rules_are_critical():
    for name in ("UndeliveredPaidOrders", "BuyerNotNotified", "ArtifactMissing"):
        assert al.by_name(name).severity == "critical"


def test_rendered_yaml_is_parseable_and_complete():
    """The rule file is GENERATED. Pimlico's rules exist only as a docker config
    with no source file — `docker cp` returns 0 bytes and the only way to read
    them is `docker exec cat`. Rules you cannot diff are rules nobody reviews."""
    text = al.render_rules_yaml()
    assert text.startswith("# GENERATED")
    for r in al.RULES:
        assert f"- alert: {r.name}" in text
    # Every rule declares whether it is verified, visibly. Match the ANNOTATION
    # KEY specifically — the word "synthetic" also appears inside a rationale,
    # and counting bare occurrences made this assertion drift by one.
    assert text.count("\n          synthetic: ") == len(al.RULES)
    # Every shipped rule now carries a synthetic, so the live file must not
    # cry UNVERIFIED — but a rule WITHOUT one must still say so, pinned with
    # a temporary rule.
    assert "UNVERIFIED" not in text, "every shipped rule has a synthetic"
    fake = al.Rule("__SyntheticlessRule__", "vector(0)", "1m", "info",
                   "test-only", "test-only", None)
    al.RULES.append(fake)
    try:
        assert "UNVERIFIED" in al.render_rules_yaml(), \
            "rules without a synthetic must say so in the file"
    finally:
        al.RULES.remove(fake)

    # Quoting must survive expressions containing quotes — otherwise the YAML
    # is silently malformed and Prometheus refuses the whole rule file, taking
    # every OTHER alert down with it.
    quoted = [r for r in al.RULES if '"' in r.expr]
    assert quoted, "no rule has a quoted label matcher; this check has stopped testing anything"
    for r in quoted:
        assert r.expr.replace('"', '\\"') in text, f"{r.name} expr was not escaped"


def test_no_two_rules_share_a_name():
    names = [r.name for r in al.RULES]
    assert len(names) == len(set(names))


async def test_records_for_deleted_rules_are_pruned(clean_db):
    """A renamed rule left its old row behind — 12 records for 11 rules.

    A stale `fired` row makes a deleted detector look like a working one; a
    stale `never_run` row would page someone about a rule nobody can find.
    Neither belongs in the table whose whole job is to say what is verified.
    """
    await db.execute(
        "INSERT INTO alert_synthetics (alert_name, last_result, last_tripped_at) "
        "VALUES ('ARuleThatWasRenamed','fired', now())")
    assert await db.fetchval(
        "SELECT count(*) FROM alert_synthetics WHERE alert_name='ARuleThatWasRenamed'") == 1

    await al.run_synthetics()

    assert await db.fetchval(
        "SELECT count(*) FROM alert_synthetics WHERE alert_name='ARuleThatWasRenamed'") == 0
    assert await db.fetchval("SELECT count(*) FROM alert_synthetics") == len(al.RULES)
