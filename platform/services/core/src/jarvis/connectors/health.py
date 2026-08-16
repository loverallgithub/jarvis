"""The health loop — where C3 stops being a design and starts being a fact.

Every connector is probed and contract-tested on a schedule, **inside the
deployed container against the real service**, and the results are the input to
the dormancy state machine. That last clause is the whole point: Pimlico's
`dormant` was a hand-set registry flag, so three sources returned zero items
every day for weeks with `dormant: []` and nothing could notice.

Three signals drive the state, and they are deliberately not interchangeable:

    probe fails       reachability / auth        2 → degraded, 4 → dormant
    contract fails    the SHAPE we parse         → dormant immediately
    zero yield        it returned nothing        3 → degraded, 5 → dormant

Only a **passing contract test** restores `live`. A successful probe does not:
reachable is not parseable, and a service that renamed a field returns 200 and
plausible zeros rather than errors.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import structlog

from .. import db
from . import base, registry
from .types import HarvestResult

log = structlog.get_logger("connectors.health")

# Bounded concurrency. Probing every connector at once is a good way to trip
# rate limits on all of them simultaneously and conclude they are all broken.
MAX_CONCURRENT = 4


@dataclass
class CheckResult:
    connector: str
    probe_ok: bool
    contract_ok: bool
    state: str
    detail: str = ""


async def check(name: str) -> CheckResult:
    """Probe, then contract-test, then record. Order matters.

    The probe runs first because it is cheap and its failure explains a contract
    failure. Running only the contract test would tell you "broken" without
    distinguishing "unreachable" from "shape changed" — two very different fixes.
    """
    impl = registry.get(name)

    pr = await impl.probe()
    await base.record_probe(name, pr.ok, pr.detail)

    if not pr.ok:
        state = await base.state_of(name)
        log.warning("health.probe_failed", connector=name, state=state,
                    detail=pr.detail[:150])
        return CheckResult(name, False, False, state, pr.detail)

    tr = await impl.contract_test()
    state = await base.record_contract_test(name, tr.ok, tr.detail)
    log.info("health.checked", connector=name, probe=pr.ok, contract=tr.ok, state=state)
    return CheckResult(name, True, tr.ok, state, tr.detail)


async def check_all(names: Optional[list[str]] = None) -> list[CheckResult]:
    targets = names or registry.implemented()
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def one(n: str) -> CheckResult:
        async with sem:
            try:
                return await check(n)
            except Exception as e:                               # noqa: BLE001
                # A connector that explodes during its own health check is
                # exactly as broken as one that fails it. Never let a single
                # bad connector abort the sweep.
                log.error("health.check_raised", connector=n, error=str(e)[:200])
                try:
                    await base.record_contract_test(n, False, f"check raised: {e}"[:300])
                except Exception:                                # noqa: BLE001
                    pass
                return CheckResult(n, False, False, "dormant", str(e)[:200])

    results = list(await asyncio.gather(*[one(n) for n in targets]))
    await db.execute(
        "UPDATE job_registry SET last_success_at = now() WHERE job_name = $1",
        "connector.contract_test")

    live = sum(1 for r in results if r.state == "live")
    log.info("health.sweep_done", checked=len(results), live=live)
    return results


# ---------------------------------------------------------------------------
# harvesting
# ---------------------------------------------------------------------------

async def harvest(name: str, limit: int = 25) -> HarvestResult:
    """Call a LIVE connector and record what it produced — including nothing.

    🔴 Only a **dormant** connector is skipped. It returns an empty result with
    a reason, and no yield is recorded — a dormant connector returning zero is
    not evidence about the source, it is evidence we did not ask, and conflating
    those makes a dormant source look like a dead one.

    ⚠️ `degraded` connectors ARE still called, and that is load-bearing. Gating
    on `state != "live"` made degraded a one-way trap: a source that hit three
    zero yields stopped being called, so its streak could never reach five
    (never dormant) and never reset (never recovered). It would sit in degraded
    for ever, which is exactly the invisible-limbo state C3 exists to abolish.
    Degraded means "failing but not written off" — you keep asking.
    """
    state = await base.state_of(name)
    if state == "dormant":
        return HarvestResult(name, [], f"not called — connector is {state}")

    impl = registry.get(name)
    try:
        result = await impl.call(limit=limit)
    except base.ConnectorError as e:
        await base.record_probe(name, False, str(e)[:300])
        log.warning("health.harvest_failed", connector=name, error=str(e)[:200])
        return HarvestResult(name, [], f"failed: {e}")

    # Zero-yield is a failure signal. This is the correction to Pimlico.
    await base.record_yield(name, result.count)

    stored = await _persist(name, result)
    log.info("health.harvested", connector=name, produced=result.count,
             admissible=len(result.admissible), stored=stored)
    return result


async def _persist(name: str, result: HarvestResult) -> int:
    """Store admissible signals. Deduplicated on (source_id, external_id).

    Inadmissible signals (the 4-content-word rule) are counted but not stored:
    bare brand names embed as mutually similar and formed a 20-member false
    cluster in Pimlico that would have cleared every gate and auto-built garbage.
    """
    source_id = await db.fetchval("SELECT id FROM sources WHERE name = $1", name)
    if source_id is None:
        log.warning("health.no_source_row", connector=name,
                    hint="the connector has an implementation but no `sources` row")
        return 0

    stored = 0
    for s in result.admissible:
        inserted = await db.fetchval(
            """
            INSERT INTO signals (source_id, external_id, concept, body, url,
                                 observed_at, raw)
            VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)
            ON CONFLICT (source_id, external_id) DO NOTHING
            RETURNING id
            """,
            source_id, s.external_id, s.concept, s.body, s.url, s.at(),
            __import__("json").dumps(s.raw, default=str))
        if inserted is None:
            continue
        stored += 1
        if s.author:
            await _upsert_voice(int(inserted), s)

    if result.count and not result.admissible:
        log.warning("health.all_inadmissible", connector=name, produced=result.count,
                    hint="every signal failed the 4-content-word admission rule")
    return stored


async def _upsert_voice(signal_id: int, s) -> None:
    """Capture who said it (DEC-004, step A1d).

    ⚠️ `do_not_contact` defaults TRUE and we do not override it here. Promotion
    to contactable requires an explicit recorded lawful basis, per voice. These
    are evidence, never a mailing list.
    """
    a = s.author
    voice_id = await db.fetchval(
        """
        INSERT INTO voices (kind, display_name, handle, platform, profile_url,
                            org_name, org_domain, last_seen)
        VALUES ($1,$2,$3,$4,$5,$6,$7, now())
        ON CONFLICT (platform, handle) DO UPDATE SET last_seen = now()
        RETURNING id
        """,
        a.kind, a.display_name or a.handle, a.handle, a.platform,
        a.profile_url, a.org_name, a.org_domain)
    if voice_id is None:
        return
    await db.execute(
        """
        INSERT INTO voice_mentions (voice_id, signal_id, stance, quote, observed_at)
        VALUES ($1,$2,$3,$4,$5)
        """,
        int(voice_id), signal_id, "reports_pain", (s.concept or "")[:1000], s.at())


async def harvest_all(limit: int = 25) -> dict[str, int]:
    """Harvest every live connector. Returns per-connector yields."""
    out: dict[str, int] = {}
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def one(n: str) -> None:
        async with sem:
            try:
                r = await harvest(n, limit=limit)
                out[n] = r.count
            except Exception as e:                               # noqa: BLE001
                log.error("health.harvest_raised", connector=n, error=str(e)[:200])
                out[n] = -1

    await asyncio.gather(*[one(n) for n in registry.implemented()])
    await db.execute(
        "UPDATE job_registry SET last_success_at = now() WHERE job_name = $1",
        "discovery.harvest")
    return out


async def summary() -> dict:
    rows = await db.fetch(
        "SELECT state, count(*) AS n FROM connector_health GROUP BY state")
    by_state = {r["state"]: int(r["n"]) for r in rows}
    return {
        "by_state": by_state,
        "live": by_state.get("live", 0),
        "implemented": len(registry.implemented()),
        "signals": int(await db.fetchval("SELECT count(*) FROM signals") or 0),
        "voices": int(await db.fetchval("SELECT count(*) FROM voices") or 0),
    }
