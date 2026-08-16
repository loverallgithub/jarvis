"""jarvis-core HTTP surface.

Deliberately small in phase 0: health, readiness, metrics, and a read-only
resume endpoint. The pipeline arrives in later phases.

⚠️ **This process runs with `--workers 1`.** That is not a performance
oversight, it is the fix for an entire failure class. Pimlico ran
`uvicorn --workers 2`, so every `Gauge.set()` landed in one worker's registry
and Prometheus scraped whichever worker answered — producing
`hermes_scan_last_success_timestamp` values that alternated between two
readings **1.5 days apart**, and an alert that could not be trusted in either
direction. Scale with swarm replicas, each single-worker, and every gauge is
service-level by construction rather than by discipline.
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

from . import db
from .config import credential_status, settings
from .connectors import base as connectors
from .runtime import checkpoints, registry

log = structlog.get_logger()

# --- metrics ---------------------------------------------------------------
# Gauges initialised to a value meaning "unknown" would be indistinguishable
# from a real zero. Pimlico had an alert on a Gauge that defaulted to 0, so
# every restart false-alarmed until someone learned to ignore it. Where a
# gauge means "count of things", 0 is genuine; where it means "when did X last
# happen", the alert expression must carry `> 0`.
JPD_UP = Gauge("jpd_up", "1 when the service has completed startup")
JPD_STEPS_REGISTERED = Gauge("jpd_steps_registered", "Steps in the registry")
JPD_CONNECTORS = Gauge("jpd_connectors_total", "Connectors by state", ["state"])
JPD_RUNS = Gauge("jpd_runs_total", "Runs by status", ["status"])
JPD_HUMAN_TASKS_OPEN = Gauge("jpd_human_tasks_open", "Open human tasks")
JPD_ORPHANED_RUNS = Gauge("jpd_orphaned_runs", "Runs 'running' with an expired lease")
JPD_SCRAPES = Counter("jpd_metric_scrapes_total", "Metrics endpoint scrapes")


CONNECTORS_LIVE = Gauge("jpd_connectors_live", "Connectors currently live")
CONNECTORS_REGRESSED = Gauge("jpd_connectors_regressed",
                             "Connectors that passed a contract test once and are not live now")
LAST_SWEEP = Gauge("jpd_connector_sweep_last_success_timestamp",
                   "Unix time of the last successful connector health sweep")
LAST_HARVEST = Gauge("jpd_harvest_last_success_timestamp",
                     "Unix time of the last successful harvest")
SIGNALS_TOTAL = Gauge("jpd_signals_total", "Signals stored")
SWEEPS = Counter("jpd_connector_sweeps_total", "Health sweeps", ["result"])

# Intervals. Contract tests every 15 min feed the dormancy state machine
# (02-ARCHITECTURE §10); harvesting is daily and its freshness alert derives
# from job_registry, not from a constant.
SWEEP_INTERVAL_S = 900
HARVEST_INTERVAL_S = 86400

_stop = asyncio.Event()


async def _sweep_loop() -> None:
    """Connector health, forever — but only in ONE replica.

    The advisory lock is taken per tick, not once at startup: an instance that
    loses its connection must stop scheduling rather than carry on because it
    won an election that is no longer true.
    """
    from .connectors import health, registry as creg
    from .runtime import singleton

    while not _stop.is_set():
        try:
            async with singleton.hold("health_sweep") as owner:
                if owner:
                    await creg.register_all()
                    results = await health.check_all()
                    SWEEPS.labels(result="ok").inc()
                    live = sum(1 for r in results if r.state == "live")
                    CONNECTORS_LIVE.set(live)
                    LAST_SWEEP.set(time.time())
                    log.info("sweep.done", checked=len(results), live=live)
        except asyncio.CancelledError:
            raise
        except Exception as e:                                   # noqa: BLE001
            SWEEPS.labels(result="error").inc()
            log.warning("sweep.failed", error=str(e)[:200])
        await asyncio.sleep(SWEEP_INTERVAL_S)


async def _harvest_loop() -> None:
    from .connectors import health
    from .runtime import singleton

    # Let the first health sweep decide who is live before harvesting; calling
    # a connector that has not been contract-tested this boot would record a
    # yield we cannot interpret.
    await asyncio.sleep(60)
    while not _stop.is_set():
        try:
            async with singleton.hold("harvest") as owner:
                if owner:
                    out = await health.harvest_all()
                    LAST_HARVEST.set(time.time())
                    log.info("harvest.done", yields=out)
        except asyncio.CancelledError:
            raise
        except Exception as e:                                   # noqa: BLE001
            log.warning("harvest.failed", error=str(e)[:200])
        await asyncio.sleep(HARVEST_INTERVAL_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Register Phase A BEFORE validating — a step whose declared test file is
    # missing must take startup down, not be skipped.
    from .discovery import steps as discovery_steps
    from .research import steps as research_steps
    from .forge import steps as forge_steps
    from .market import steps as market_steps
    discovery_steps.register()
    research_steps.register()
    forge_steps.register()
    market_steps.register()

    problems = registry.validate_registry()
    if problems:
        # Fail startup loudly. A step with a missing test is a step whose
        # failure nobody would notice, which is the defect this whole design
        # exists to prevent.
        raise RuntimeError(f"step registry invalid: {problems}")

    await db.pool()
    if not await db.schema_ready():
        log.error("startup.schema_not_ready",
                  hint="run `jpd db migrate` — refusing to serve on an unmigrated schema")
        raise RuntimeError("schema not ready")

    JPD_STEPS_REGISTERED.set(len(registry.all_steps()))
    JPD_UP.set(1)

    _stop.clear()
    app.state.loops = [asyncio.create_task(_sweep_loop()),
                       asyncio.create_task(_harvest_loop())]

    log.info("startup.complete", service=settings.service, version=settings.version,
             steps=len(registry.all_steps()))
    yield

    _stop.set()
    for t in getattr(app.state, "loops", []):
        t.cancel()
    await asyncio.gather(*getattr(app.state, "loops", []), return_exceptions=True)
    JPD_UP.set(0)
    await db.close()


app = FastAPI(title="jarvis-core", version=settings.version, lifespan=lifespan)


@app.get("/health")
async def health():
    """Liveness. Deliberately does NOT touch the database — a health check
    that fails when postgres blips causes a restart storm that makes the
    outage worse."""
    return {"ok": True, "service": settings.service, "version": settings.version}


@app.get("/ready")
async def ready():
    """Readiness. This one DOES check the database, because a process that
    cannot reach postgres must not receive traffic."""
    try:
        await db.fetchval("SELECT 1")
    except Exception as e:                                       # noqa: BLE001
        raise HTTPException(503, f"database unreachable: {type(e).__name__}")
    if not await db.schema_ready():
        raise HTTPException(503, "schema not migrated")
    return {"ready": True}


@app.get("/status")
async def status():
    """Booleans only for credentials. Never a value, never a prefix —
    this endpoint is reachable from outside."""
    return {
        "service": settings.service,
        "version": settings.version,
        "env": settings.env,
        "steps_registered": len(registry.all_steps()),
        "credentials": credential_status(),
        "connectors": await connectors.snapshot(),
    }


@app.get("/resume")
async def resume(run: int | None = None):
    return await checkpoints.resume_report(run)


@app.get("/metrics")
async def metrics():
    JPD_SCRAPES.inc()
    await _refresh_gauges()
    return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)


async def _refresh_gauges() -> None:
    """Refresh service-level gauges at scrape time.

    Reading from the database at scrape time (rather than setting gauges from
    request handlers) is the second half of the single-worker fix: the value
    served is derived from shared state, so every replica reports the same
    number and a rolling deploy cannot make an alert flap.
    """
    try:
        for state in ("live", "degraded", "dormant"):
            n = await db.fetchval(
                "SELECT count(*) FROM connector_health WHERE state = $1", state)
            JPD_CONNECTORS.labels(state=state).set(int(n or 0))

        for st in ("running", "paused", "blocked_on_human", "completed", "failed", "killed"):
            n = await db.fetchval("SELECT count(*) FROM runs WHERE status = $1", st)
            JPD_RUNS.labels(status=st).set(int(n or 0))

        JPD_HUMAN_TASKS_OPEN.set(int(await db.fetchval(
            "SELECT count(*) FROM human_tasks WHERE status = 'open'") or 0))
        CONNECTORS_LIVE.set(int(await db.fetchval(
            "SELECT count(*) FROM connector_health WHERE state = 'live'") or 0))
        SIGNALS_TOTAL.set(int(await db.fetchval("SELECT count(*) FROM signals") or 0))
        from .observability.alerts import regressed_count
        CONNECTORS_REGRESSED.set(await regressed_count())
        JPD_ORPHANED_RUNS.set(int(await db.fetchval(
            "SELECT count(*) FROM runs WHERE status = 'running' AND "
            "(lease_expires_at IS NULL OR lease_expires_at < now())") or 0))
    except Exception as e:                                       # noqa: BLE001
        # Never let a metrics refresh take the endpoint down — but never let
        # it fail silently either. A stale gauge with a logged error is
        # recoverable; a 500 on /metrics blinds the whole monitoring stack.
        log.warning("metrics.refresh_failed", error=str(e))
