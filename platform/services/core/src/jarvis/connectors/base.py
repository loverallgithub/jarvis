"""The connector contract and the dormancy state machine (C3).

Every external dependency — API, RSS, headless browser, **or human** —
implements the same three methods. `kind="human"` being a first-class
connector is not a workaround: roughly two-thirds of the owned product
portfolio has no API at all, so an architecture that can only consume APIs can
use a third of what has been paid for.

────────────────────────────────────────────────────────────────────────────
WHY `call()` RETURNS A TYPED ARTIFACT AND NEVER A STRING
────────────────────────────────────────────────────────────────────────────
Pimlico's entire LinkedIn incident was possible because
`_route_sintra_output(output_type, text: str, ...)` accepted a string. When
Sintra was Cloudflare-blocked, the except-branch returned
`f"[Automation failed: {e}]"` — a perfectly ordinary string, indistinguishable
from success at every layer downstream. It was persisted as market
intelligence, as a video script, and into two publish queues, and on six
consecutive days it was posted to a live LinkedIn account.

A connector returns a usable typed result or it raises. It never describes its
own failure in the same type it returns success in.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Protocol, runtime_checkable

import structlog

from .. import db

log = structlog.get_logger()

ConnectorKind = Literal["api", "rss", "browser", "human"]
HealthState = Literal["live", "degraded", "dormant"]

# Transition thresholds (02-ARCHITECTURE §3). Consecutive probe failures walk a
# connector down; only a PASSING CONTRACT TEST walks it back up. A probe
# succeeding is not enough — reachable is not the same as parseable.
PROBE_FAILS_TO_DEGRADED = 2
PROBE_FAILS_TO_DORMANT = 4

# Zero-yield is a FAILURE signal, not a quiet success. Pimlico's google_trends,
# indie_hackers and app_store_reviews returned 0 items every day with
# `dormant: []` — because `dormant` was a hand-set flag nobody ever set.
ZERO_YIELD_TO_DEGRADED = 3
ZERO_YIELD_TO_DORMANT = 5


class ConnectorError(RuntimeError):
    """A connector could not produce a usable result. Always raised, never returned."""


@dataclass
class ProbeResult:
    ok: bool
    latency_ms: int = 0
    detail: str = ""


@dataclass
class TestResult:
    # Not a pytest test class — without this pytest tries to collect it and
    # warns on every run, which trains people to ignore warnings.
    __test__ = False

    """Contract test: does the response have the SHAPE we parse?

    Distinct from a probe on purpose. A service can be up, authenticated and
    returning 200 while having renamed the field we depend on — that is the
    failure mode that produces plausible zeros instead of errors.
    """
    ok: bool
    detail: str = ""
    observed_shape: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Connector(Protocol):
    name: str
    kind: ConnectorKind

    async def probe(self) -> ProbeResult: ...
    async def contract_test(self) -> TestResult: ...
    async def call(self, req: Any) -> Any: ...


# ---------------------------------------------------------------------------
# state machine
# ---------------------------------------------------------------------------

async def state_of(connector: str) -> HealthState:
    """An unknown connector is DORMANT, never live. Absent must be safe."""
    s = await db.fetchval(
        "SELECT state FROM connector_health WHERE connector = $1", connector)
    return s or "dormant"  # type: ignore[return-value]


async def register(connector: str, kind: ConnectorKind, note: str = "") -> None:
    await db.execute(
        """
        INSERT INTO connector_health (connector, kind, state, evidence)
        VALUES ($1, $2, 'dormant', jsonb_build_object('note', $3::text))
        ON CONFLICT (connector) DO NOTHING
        """,
        connector, kind, note)


async def record_probe(connector: str, ok: bool, detail: str = "") -> HealthState:
    row = await db.fetchrow(
        "SELECT state, fail_streak FROM connector_health WHERE connector = $1", connector)
    if row is None:
        raise ConnectorError(f"connector {connector!r} is not registered")

    state: HealthState = row["state"]
    streak = 0 if ok else int(row["fail_streak"]) + 1

    if not ok:
        if streak >= PROBE_FAILS_TO_DORMANT:
            state = "dormant"
        elif streak >= PROBE_FAILS_TO_DEGRADED and state == "live":
            state = "degraded"
        log.warning("connector.probe_failed", connector=connector,
                    fail_streak=streak, state=state, detail=detail[:200])
    # NOTE: a successful probe deliberately does NOT restore `live`. Only a
    # passing contract test does. Reachable != parseable, and it was exactly
    # that conflation that let broken sources look healthy in Pimlico.

    await db.execute(
        """
        UPDATE connector_health
           SET fail_streak = $2, state = $3, last_probe_at = now(), updated_at = now(),
               evidence = evidence || jsonb_build_object('last_probe', $4::text)
         WHERE connector = $1
        """,
        connector, streak, state, detail[:500])
    return state


async def record_contract_test(connector: str, ok: bool, detail: str = "") -> HealthState:
    """The only transition back to `live`."""
    state: HealthState = "live" if ok else "dormant"
    await db.execute(
        """
        UPDATE connector_health
           SET state = $2, last_contract_at = now(), updated_at = now(),
               fail_streak = CASE WHEN $3 THEN 0 ELSE fail_streak END,
               zero_yield_streak = CASE WHEN $3 THEN 0 ELSE zero_yield_streak END,
               evidence = evidence || jsonb_build_object('last_contract', $4::text)
         WHERE connector = $1
        """,
        connector, state, ok, detail[:500])
    if not ok:
        log.error("connector.contract_failed", connector=connector, detail=detail[:200])
    return state


async def record_yield(connector: str, items: int) -> HealthState:
    """Zero-yield is a failure signal. This is C3's core correction.

    A source that returns nothing forever is indistinguishable from a source
    that is broken, and Pimlico's registry could not express the difference
    because `dormant` was a flag a human set by hand. Here the state is
    COMPUTED from observed yield, so a silently-dead source flags itself.
    """
    row = await db.fetchrow(
        "SELECT state, zero_yield_streak FROM connector_health WHERE connector = $1",
        connector)
    if row is None:
        raise ConnectorError(f"connector {connector!r} is not registered")

    state: HealthState = row["state"]
    streak = 0 if items > 0 else int(row["zero_yield_streak"]) + 1

    if items <= 0:
        if streak >= ZERO_YIELD_TO_DORMANT:
            state = "dormant"
        elif streak >= ZERO_YIELD_TO_DEGRADED and state == "live":
            state = "degraded"
        log.warning("connector.zero_yield", connector=connector,
                    zero_yield_streak=streak, state=state)

    await db.execute(
        """
        UPDATE connector_health
           SET zero_yield_streak = $2, state = $3, updated_at = now()
         WHERE connector = $1
        """,
        connector, streak, state)
    return state


async def quarantine(connector: str, payload: str, reason: str,
                     run_id: Optional[int] = None) -> int:
    """Send a malformed payload to the dead letter table.

    Nothing in `dead_letter` can reach a publish path — that is enforced by
    the publish step's own acceptance predicate, not by convention.
    """
    log.warning("connector.quarantined", connector=connector, reason=reason[:200])
    return int(await db.fetchval(
        "INSERT INTO dead_letter (connector, payload_raw, reason, run_id) "
        "VALUES ($1, $2, $3, $4) RETURNING id",
        connector, payload[:20000], reason[:500], run_id))


async def snapshot() -> list[dict]:
    rows = await db.fetch(
        "SELECT connector, kind, state, fail_streak, zero_yield_streak, "
        "last_probe_at, last_contract_at FROM connector_health ORDER BY state, connector")
    return [dict(r) for r in rows]
