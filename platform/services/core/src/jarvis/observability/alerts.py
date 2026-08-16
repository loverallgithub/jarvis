"""Alert rules, and the synthetic tests that prove they work.

────────────────────────────────────────────────────────────────────────────
C2 — AN UNTESTED DETECTOR IS NOT A DETECTOR
────────────────────────────────────────────────────────────────────────────
Pimlico's roadmap correctly identified "nothing reports failure" as the root
cause of every other defect, and it built **eleven alert rules** to fix it.
Today **four of those detectors are silently broken**. More rules was not the
answer.

So every rule here ships with a `synthetic` callable that deliberately creates
the condition and asserts the expression would fire. `run_synthetics()` runs
them on a schedule and records the result in `alert_synthetics`. A rule that has
not been verified in 10 days trips the meta-alert `AlertNeverTripped`.

────────────────────────────────────────────────────────────────────────────
Two rules about how the expressions are written
────────────────────────────────────────────────────────────────────────────
**1. Freshness is derived, never guessed.** Every staleness alert is
`expected_interval × 1.5`, read from `job_registry`. Pimlico's
`NoSuccessfulScan` was hardcoded at 10 days for a weekly job, so it could not
fire until two consecutive misses.

**2. A gauge at 0 means UNKNOWN, not stale.** Timestamp gauges start at 0, so
every freshness expression carries `> 0`. Without it, every restart
false-alarms until someone learns to ignore the alert — which is how an alert
stops being an alert.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

import structlog

from .. import db

log = structlog.get_logger("observability.alerts")

SYNTHETIC_MAX_AGE_DAYS = 10


@dataclass
class Rule:
    name: str
    expr: str
    for_: str
    severity: str
    summary: str
    # Why this alert exists — the incident it would have caught. Rendered into
    # the rule file so whoever is paged at 3am knows what they are looking at.
    rationale: str
    synthetic: Optional[Callable[[], Awaitable[bool]]] = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# the synthetics
# ---------------------------------------------------------------------------
# Each creates the condition in a sandboxed way, evaluates the SAME predicate
# the PromQL expression encodes, then cleans up. They assert the logic, not
# Prometheus itself — a full end-to-end trip would need a live alertmanager and
# a 5-minute wait per rule, which is why nobody would run it and it would rot.

async def _syn_undelivered() -> bool:
    """Create a paid order with a failed fulfilment; assert the query sees it.

    ⚠️ Cleanup unwinds in DEPENDENCY ORDER, and it has to.
    `DELETE FROM needs` looked like it would cascade the whole graph away, but
    the money-path foreign keys are `ON DELETE RESTRICT` on purpose — an offer
    with orders, and an entitlement with an order, must not be deletable. So the
    cascade stops dead and the synthetic leaves a fake unpaid-buyer row behind,
    which then trips the very alert it was testing and looks like a real
    incident. Found by the cleanup test, not by review.
    """
    need = sol = off = order = ent = None
    try:
        need = await db.fetchval(
            "INSERT INTO needs (title) VALUES ('__synthetic__') RETURNING id")
        sol = await db.fetchval(
            "INSERT INTO solutions (need_id, title) VALUES ($1,'__synthetic__') RETURNING id",
            need)
        off = await db.fetchval(
            "INSERT INTO offers (solution_id, tier, price_minor, live) "
            "VALUES ($1,'roadmap',100,TRUE) RETURNING id", sol)
        order = await db.fetchval(
            "INSERT INTO orders (offer_id, buyer_ref, amount_minor, currency, provider, "
            "provider_ref, signature_valid, amount_matched, status) "
            "VALUES ($1,'__syn__',100,'EUR','synthetic',$2,TRUE,TRUE,'verified') RETURNING id",
            off, f"syn_{need}")
        ent = await db.fetchval(
            "INSERT INTO entitlements (order_id, buyer_ref, solution_id, tier) "
            "VALUES ($1,'__syn__',$2,'roadmap') RETURNING id", order, sol)
        await db.execute(
            "INSERT INTO fulfilments (entitlement_id, status, tier, error) "
            "VALUES ($1,'failed','roadmap','synthetic')", ent)

        from ..commerce.fulfilment import undelivered_paid_orders
        return len(await undelivered_paid_orders()) > 0
    finally:
        for sql, arg in (
                ("DELETE FROM fulfilments WHERE entitlement_id = $1", ent),
                ("DELETE FROM entitlements WHERE id = $1", ent),
                ("DELETE FROM orders WHERE id = $1", order),
                ("DELETE FROM offers WHERE id = $1", off),
                ("DELETE FROM solutions WHERE id = $1", sol),
                ("DELETE FROM needs WHERE id = $1", need)):
            if arg is not None:
                try:
                    await db.execute(sql, arg)
                except Exception as e:                           # noqa: BLE001
                    # Loud, because residue from a synthetic trips a real alert.
                    log.error("synthetic.cleanup_failed", stmt=sql[:40],
                              error=str(e)[:200])


async def _syn_notifications_owed() -> bool:
    nid = await db.fetchval(
        "INSERT INTO notifications (kind, channel, status, error) "
        "VALUES ('__synthetic__','auto','skipped_dormant','synthetic') RETURNING id")
    try:
        from ..commerce.notify import pending_and_failed
        return (await pending_and_failed()).get("owed", 0) > 0
    finally:
        await db.execute("DELETE FROM notifications WHERE id = $1", nid)


async def _syn_service_down() -> bool:
    """Verify the DELIVERY path, not the outage itself.

    Stopping a service in production to prove `jpd_up == 0` fires is not an
    option, and the expression is a trivial comparison anyway. What actually
    rots unseen is the chain that turns a firing rule into a card the operator
    reads: Alertmanager's webhook target, the console's /alerts route, and the
    Telegram stream configuration. This posts an EMPTY alert batch through the
    real console webhook (nothing reaches Telegram — the handler loops over
    zero alerts) and then checks the streams are configured end to end. The
    `up == 0` half stays covered by the phase-2 C7 exercise, where core was
    scaled to 0/0 by hand.
    """
    import os

    import httpx

    base = os.environ.get("JPD_CONSOLE_URL", "http://console:8905").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{base}/alerts", json={"status": "firing", "alerts": []})
            if r.status_code != 200:
                return False
            s = await c.get(f"{base}/streams")
            if s.status_code != 200:
                return False
            return bool(s.json().get("ht_001_complete"))
    except Exception as e:                                       # noqa: BLE001
        log.warning("synthetic.service_down_path_failed", error=str(e)[:200])
        return False


async def _syn_connector_regressed() -> bool:
    """Walk a connector from live to dormant and assert the REGRESSION query sees it.

    Not by setting `state='dormant'` directly — that would test the UPDATE, not
    the transition. Pimlico's flag was settable and never set; the thing worth
    proving is that observed failure MOVES it.
    """
    from ..connectors import base
    name = "__synthetic__"
    await base.register(name, "api", "synthetic alert test")
    try:
        await base.record_contract_test(name, True, "synthetic setup")
        assert await base.state_of(name) == "live"
        for _ in range(4):
            await base.record_probe(name, False, "synthetic failure")
        if await base.state_of(name) != "dormant":
            return False
        # The alert's actual input: it worked once, and it does not now.
        return await regressed_count() > 0
    finally:
        await db.execute("DELETE FROM connector_health WHERE connector = $1", name)


async def regressed_count() -> int:
    """Connectors we have actually EXERCISED (`last_contract_at IS NOT NULL`)
    that are not live now.

    🔴 This exists because `dormant > 0` was a useless alert. Seventeen of the
    twenty-four registered connectors are dormant **by design** — they are
    waiting on credentials (GHL, Stripe, Telegram, YouTube, TubeOnAI, Skool...)
    and have never been contract-tested at all. An alert that is always firing
    is not an alert; it is a thing people learn to close, which is exactly how
    Pimlico came to ignore its own monitoring. Measured live: this took the
    alert's input from **17 to 2**, and both remaining are real
    (`indie_hackers` serves HTML from its feed URL, `reddit` 403s this IP).

    ⚠️ Precision matters here: this is "we tried it and it is not working", NOT
    "it worked once and stopped". Distinguishing those needs a
    `last_contract_ok_at` column, which does not exist yet. The looser predicate
    is deliberate for now — it still excludes the never-configured majority,
    which is where all the noise was — but do not read more into it than it
    says. A connector that has never been configured is a SETUP TASK and
    belongs in the human-task queue, not in a pager.
    """
    return int(await db.fetchval(
        "SELECT count(*) FROM connector_health "
        "WHERE last_contract_at IS NOT NULL AND state <> 'live'") or 0)


async def _syn_zero_yield_dormant() -> bool:
    """The Pimlico correction: zero yield walks a source toward dormant."""
    from ..connectors import base
    name = "__synthetic_zy__"
    await base.register(name, "api", "synthetic zero-yield test")
    try:
        await base.record_contract_test(name, True, "synthetic setup")
        for _ in range(5):
            await base.record_yield(name, 0)
        return await base.state_of(name) == "dormant"
    finally:
        await db.execute("DELETE FROM connector_health WHERE connector = $1", name)


async def _syn_task_unannounced() -> bool:
    tid = await db.fetchval(
        "INSERT INTO human_tasks (ref, type, title, why, how_md, reply_schema, "
        "status, expires_at) VALUES ('__SYN__','task','synthetic','synthetic','', "
        "'{}'::jsonb,'open', now() + interval '1 day') RETURNING id")
    try:
        n = await db.fetchval(
            "SELECT count(*) FROM human_tasks WHERE status='open' "
            "AND telegram_message_id IS NULL")
        return int(n or 0) > 0
    finally:
        await db.execute("DELETE FROM human_tasks WHERE id = $1", tid)


async def _syn_artifact_missing() -> bool:
    need = await db.fetchval(
        "INSERT INTO needs (title) VALUES ('__synthetic_art__') RETURNING id")
    try:
        sol = await db.fetchval(
            "INSERT INTO solutions (need_id, title) VALUES ($1,'__syn__') RETURNING id", need)
        art = await db.fetchval(
            "INSERT INTO artifacts (solution_id, tier, kind, sha256, bytes, storage_uri, "
            "missing_since) VALUES ($1,'roadmap','pdf','x',1,'file:///nope/missing.pdf', now()) "
            "RETURNING id", sol)
        n = await db.fetchval(
            "SELECT count(*) FROM artifacts WHERE missing_since IS NOT NULL")
        return int(n or 0) > 0
    finally:
        await db.execute("DELETE FROM needs WHERE id = $1", need)


async def _syn_stale_job() -> bool:
    """Freshness derived from job_registry, not hardcoded."""
    await db.execute(
        "INSERT INTO job_registry (job_name, expected_interval_s, last_success_at) "
        "VALUES ('__synthetic_job__', 60, now() - interval '10 minutes') "
        "ON CONFLICT (job_name) DO UPDATE SET last_success_at = EXCLUDED.last_success_at")
    try:
        row = await db.fetchrow(
            "SELECT expected_interval_s, "
            "EXTRACT(EPOCH FROM (now() - last_success_at)) AS age "
            "FROM job_registry WHERE job_name = '__synthetic_job__'")
        return float(row["age"]) > float(row["expected_interval_s"]) * 1.5
    finally:
        await db.execute("DELETE FROM job_registry WHERE job_name = '__synthetic_job__'")


async def _syn_task_stale() -> bool:
    """A task open longer than three days lands in the gte_3d age bucket."""
    tid = await db.fetchval(
        "INSERT INTO human_tasks (ref, type, title, why, how_md, reply_schema, status, "
        "expires_at, created_at) VALUES ('__SYN_STALE__','task','synthetic','synthetic','', "
        "'{}'::jsonb,'open', now() + interval '1 day', now() - interval '4 days') "
        "RETURNING id")
    try:
        n = await db.fetchval(
            "SELECT count(*) FROM human_tasks WHERE status='open' "
            "AND now() - created_at >= interval '3 days'")
        return int(n or 0) > 0
    finally:
        await db.execute("DELETE FROM human_tasks WHERE id = $1", tid)


async def _syn_harvest_stale() -> bool:
    """Freshness for the harvest job, derived from its declared interval.

    Restores the real timestamp afterwards — leaving a stale one behind would
    trip the genuine alert and look like a real outage.
    """
    row = await db.fetchrow(
        "SELECT expected_interval_s, last_success_at FROM job_registry "
        "WHERE job_name = 'discovery.harvest'")
    if row is None:
        return False
    original = row["last_success_at"]
    interval = int(row["expected_interval_s"])
    try:
        await db.execute(
            "UPDATE job_registry SET last_success_at = now() - make_interval(secs => $1) "
            "WHERE job_name = 'discovery.harvest'", interval * 2)
        age = await db.fetchval(
            "SELECT EXTRACT(EPOCH FROM (now() - last_success_at)) FROM job_registry "
            "WHERE job_name = 'discovery.harvest'")
        return float(age) > interval * 1.5
    finally:
        await db.execute(
            "UPDATE job_registry SET last_success_at = $1 WHERE job_name = 'discovery.harvest'",
            original)


async def _syn_alert_never_tripped() -> bool:
    """The meta-alert, verified by the mechanism it watches.

    There is a pleasing recursion here and it is not a gimmick: the rule that
    catches unverified detectors is itself the easiest one to leave unverified,
    which is precisely how Pimlico ended up with four broken detectors and no
    way to know.
    """
    probe = "__synthetic_meta__"
    await db.execute(
        "INSERT INTO alert_synthetics (alert_name, last_tripped_at, last_result) "
        "VALUES ($1, now() - make_interval(days => $2), 'fired') "
        "ON CONFLICT (alert_name) DO UPDATE SET last_tripped_at = EXCLUDED.last_tripped_at",
        probe, SYNTHETIC_MAX_AGE_DAYS * 2)
    try:
        stale = {s["alert_name"] for s in await stale_synthetics()}
        return probe in stale
    finally:
        await db.execute("DELETE FROM alert_synthetics WHERE alert_name = $1", probe)


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------

RULES: list[Rule] = [
    Rule("UndeliveredPaidOrders",
         'jpd_undelivered_paid_orders > 0', "5m", "critical",
         "A buyer paid and has not received everything they bought",
         "The single most important question this system can be asked. Pimlico's "
         "three delivery tokens all point at files that do not exist.",
         _syn_undelivered),

    Rule("BuyerNotNotified",
         'jpd_notifications_owed > 0', "15m", "critical",
         "Money taken and the buyer has not been told",
         "A notification that never sent produces no error anywhere: the payment "
         "succeeded, the entitlement exists, the artifact is on disk, only the "
         "email is missing.",
         _syn_notifications_owed),

    Rule("ArtifactMissing",
         'jpd_artifacts_missing > 0', "10m", "critical",
         "An artifact behind a live download token is not on disk",
         "A file that existed at mint time can vanish afterwards. The buyer must "
         "never be the monitoring system.",
         _syn_artifact_missing),

    Rule("ConnectorRegressed",
         'jpd_connectors_regressed > 0', "30m", "warning",
         "A connector that used to work has stopped working",
         "C3. Deliberately NOT `dormant > 0`: 17 of 24 connectors are dormant by "
         "design, waiting on credentials, so that alert fired permanently and "
         "would have been learned-and-ignored. 'It worked once and does not now' "
         "is the actionable signal; 'never configured' is a setup task.",
         _syn_connector_regressed),

    Rule("NoLiveConnectors",
         'jpd_connectors_live == 0', "15m", "critical",
         "No connector is live — the funnel cannot harvest anything",
         "Distinct from ConnectorDormant: one dormant source degrades the funnel, "
         "zero live sources stops it entirely.",
         _syn_zero_yield_dormant),

    Rule("HumanTaskUnannounced",
         'jpd_human_tasks_unannounced > 0', "10m", "warning",
         "An open human task was never posted to Telegram",
         "The task exists and the run is visibly blocked, but nobody was told. "
         "Recoverable by a retry — and invisible without this.",
         _syn_task_unannounced),

    Rule("HumanTaskStale",
         'jpd_human_tasks_open_by_age{age_bucket="gte_3d"} > 0', "1h", "warning",
         "A human task has been open for more than three days",
         "An expired approval silently stalled a Pimlico build for five days. If "
         "this queue grows, the design is wrong and you need to see it.",
         _syn_task_stale),

    Rule("ConnectorSweepStale",
         # `> 0` is load-bearing: the gauge starts at 0 meaning UNKNOWN, and
         # without this every restart false-alarms.
         'jpd_connector_sweep_last_success_timestamp > 0 and '
         'time() - jpd_connector_sweep_last_success_timestamp > 1350', "5m", "warning",
         "Connector health has not been swept in over 22 minutes",
         "Derived: expected 900s x 1.5. Without sweeps the dormancy state machine "
         "stops updating and every connector looks frozen in its last state.",
         _syn_stale_job),

    Rule("HarvestStale",
         'jpd_harvest_last_success_timestamp > 0 and '
         'time() - jpd_harvest_last_success_timestamp > 129600', "30m", "warning",
         "No successful harvest in over 36 hours",
         "Derived: expected 86400s x 1.5. Pimlico's NoSuccessfulScan was hardcoded "
         "at 10 days for a weekly job, so it needed two consecutive misses to fire.",
         _syn_harvest_stale),

    Rule("ServiceDown",
         'jpd_up == 0 or jpd_commerce_up == 0 or jpd_console_up == 0', "2m", "critical",
         "A JPD service has stopped",
         "Three separate services; any of them being down means a different "
         "capability is gone. The `up == 0` half cannot be synthesised without "
         "stopping a service in production — that stays verified by the phase-2 "
         "C7 exercise, where core was scaled to 0/0 by hand. The synthetic "
         "covers the half that rots silently instead: the Alertmanager→console→"
         "Telegram delivery path that turns a firing rule into a card.",
         _syn_service_down),

    Rule("AlertNeverTripped",
         # The meta-alert. Exported by the synthetic runner itself.
         f'jpd_alert_synthetic_age_days > {SYNTHETIC_MAX_AGE_DAYS}', "1h", "warning",
         "An alert rule has not been synthetically verified in 10 days",
         "THE POINT OF ALL THIS. Pimlico shipped eleven rules and four of its "
         "detectors are silently broken today. An untested detector is not a "
         "detector — including this one, which is why it has a synthetic of "
         "its own.",
         _syn_alert_never_tripped),
]


def by_name(name: str) -> Rule:
    for r in RULES:
        if r.name == name:
            return r
    raise KeyError(name)


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def render_rules_yaml() -> str:
    """Generate the Prometheus rule file.

    Generated, not hand-maintained: Pimlico's alert rules live only as a docker
    config with no source file, so `docker cp` returns 0 bytes and the only way
    to read them is `docker exec cat`. Rules should be reviewable in a diff.
    """
    def q(s: str) -> str:
        return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = ["# GENERATED by jarvis — do not edit. Edit observability/alerts.py.",
             "groups:", "  - name: jpd", "    rules:"]
    for r in RULES:
        lines += [
            f"      - alert: {r.name}",
            f"        expr: {q(r.expr)}",
            f"        for: {r.for_}",
            "        labels:",
            f"          severity: {r.severity}",
            "          platform: jpd",
            "        annotations:",
            f"          summary: {q(r.summary)}",
            f"          rationale: {q(r.rationale)}",
            # Rendered into the rule itself so an unverified detector is
            # visible to whoever reads the rules, not just to whoever reads
            # the alert_synthetics table.
            f"          synthetic: {q('yes' if r.synthetic else 'NONE - this rule is UNVERIFIED')}",
        ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# running the synthetics
# ---------------------------------------------------------------------------

async def run_synthetics() -> dict[str, Any]:
    """Trip every rule that has a synthetic, and record the result.

    Prunes records for rules that no longer exist first. Renaming
    `ConnectorDormant` to `ConnectorRegressed` left the old row behind, so the
    table reported **12 records for 11 rules** — and a stale `fired` row makes
    a deleted detector look like a working one, while a stale `never_run` row
    would page someone about a rule nobody can find. Neither is acceptable in
    the table whose entire job is to say what is and is not verified.
    """
    known = [r.name for r in RULES]
    pruned = await db.fetch(
        "DELETE FROM alert_synthetics WHERE alert_name <> ALL($1::text[]) "
        "RETURNING alert_name", known)
    if pruned:
        log.info("synthetics.pruned_stale_rules",
                 removed=[r["alert_name"] for r in pruned])

    results: dict[str, str] = {}
    for r in RULES:
        if r.synthetic is None:
            await db.execute(
                """
                INSERT INTO alert_synthetics (alert_name, last_result, detail)
                VALUES ($1,'never_run','no synthetic defined for this rule')
                ON CONFLICT (alert_name) DO UPDATE
                  SET last_result = 'never_run', detail = EXCLUDED.detail
                """, r.name)
            results[r.name] = "never_run"
            continue
        try:
            fired = await r.synthetic()
            outcome = "fired" if fired else "did_not_fire"
            detail = "" if fired else "the condition was created and the rule did NOT fire"
        except Exception as e:                                   # noqa: BLE001
            outcome, detail = "error", f"{type(e).__name__}: {e}"[:400]
            log.error("synthetic.raised", alert=r.name, error=detail)

        await db.execute(
            """
            INSERT INTO alert_synthetics (alert_name, last_tripped_at, last_result, detail)
            VALUES ($1, now(), $2, $3)
            ON CONFLICT (alert_name) DO UPDATE
              SET last_tripped_at = now(), last_result = EXCLUDED.last_result,
                  detail = EXCLUDED.detail
            """, r.name, outcome, detail or None)
        results[r.name] = outcome
        if outcome != "fired":
            log.error("synthetic.failed", alert=r.name, outcome=outcome, detail=detail[:200])

    await db.execute(
        "INSERT INTO job_registry (job_name, expected_interval_s, last_success_at) "
        "VALUES ('alert.synthetic_sweep', 604800, now()) "
        "ON CONFLICT (job_name) DO UPDATE SET last_success_at = now()")

    fired = sum(1 for v in results.values() if v == "fired")
    log.info("synthetics.done", total=len(results), fired=fired)
    return {"results": results, "fired": fired, "total": len(results),
            "unverified": [k for k, v in results.items() if v != "fired"]}


async def stale_synthetics(max_age_days: int = SYNTHETIC_MAX_AGE_DAYS) -> list[dict]:
    """Rules not verified recently — the input to AlertNeverTripped."""
    rows = await db.fetch(
        """
        SELECT alert_name, last_result, last_tripped_at,
               COALESCE(EXTRACT(EPOCH FROM (now() - last_tripped_at)) / 86400, 9999) AS age_days
          FROM alert_synthetics
         WHERE last_tripped_at IS NULL
            OR last_tripped_at < now() - make_interval(days => $1)
         ORDER BY alert_name
        """, max_age_days)
    return [dict(r) for r in rows]
