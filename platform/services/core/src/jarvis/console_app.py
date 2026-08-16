"""jarvis-console — the operator surface, deployed independently.

C7, stated plainly: **when core is down you must still be able to see and act.**
Under Pimlico a hermes roll blinded the operator for ~90 seconds and any real
outage blinded them entirely, because the operator surface lived inside the
thing that was broken.

This process depends on postgres and Telegram. It does not call core, does not
import the step engine, and does not care whether core is running. A run blocked
on a human task stays visible and answerable through a complete core outage.

It owns three loops:
  · **poll** replies (long-poll, clamped cursor)
  · **expire** overdue tasks and announce it
  · **post** cards that could not be announced when they were created
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

from . import db
from .config import settings
from .console import poller, tasks
from .console.telegram import TelegramClient, stream_status

log = structlog.get_logger("console")

CONSOLE_UP = Gauge("jpd_console_up", "1 when the console has completed startup")
TASKS_OPEN = Gauge("jpd_human_tasks_open_by_age", "Open human tasks", ["age_bucket"])
TASKS_UNANNOUNCED = Gauge("jpd_human_tasks_unannounced",
                          "Open tasks whose card was never posted — MUST be 0")
TASKS_EXPIRED = Gauge("jpd_human_tasks_expired", "Tasks that expired unanswered")
STREAMS_CONFIGURED = Gauge("jpd_telegram_streams_configured",
                           "Streams with a chat_id (6 = HT-001 complete)")
POLL_CYCLES = Counter("jpd_console_poll_cycles_total", "Poller cycles", ["result"])
REPLIES = Counter("jpd_console_replies_total", "Inbound replies", ["result"])
ALERTS_RECEIVED = Counter("jpd_alerts_received_total", "Alerts from alertmanager",
                          ["severity", "status"])
# The meta-alert's input: how long since each rule was synthetically verified.
# Exported here rather than in core because the console is the service whose
# job is to notice things nobody else will.
SYNTHETIC_AGE = Gauge("jpd_alert_synthetic_age_days",
                      "Days since an alert rule was synthetically verified", ["alert"])
SYNTHETIC_RESULT = Gauge("jpd_alert_synthetic_ok",
                         "1 if the rule's last synthetic run fired as expected", ["alert"])

_stop = asyncio.Event()


async def _poll_loop() -> None:
    """Long-poll for replies, forever.

    Errors are swallowed and retried with a delay: a Telegram outage must not
    kill the console, because the console is what tells you about outages.
    """
    while not _stop.is_set():
        try:
            r = await poller.poll_once(timeout_s=25)
            POLL_CYCLES.labels(result="ok").inc()
            if r.get("accepted"):
                REPLIES.labels(result="accepted").inc(r["accepted"])
            if r.get("matched", 0) - r.get("accepted", 0) > 0:
                REPLIES.labels(result="rejected").inc(r["matched"] - r["accepted"])
            if r.get("skipped_no_token"):
                await asyncio.sleep(30)      # nothing to poll; do not hot-spin
        except asyncio.CancelledError:
            raise
        except Exception as e:                                   # noqa: BLE001
            POLL_CYCLES.labels(result="error").inc()
            log.warning("console.poll_loop_error", error=str(e)[:200])
            await asyncio.sleep(10)


async def _housekeeping_loop() -> None:
    while not _stop.is_set():
        try:
            expired = await tasks.expire_due()
            if expired:
                log.warning("console.tasks_expired", count=len(expired),
                            refs=[e["ref"] for e in expired])
            await tasks.post_pending()
        except asyncio.CancelledError:
            raise
        except Exception as e:                                   # noqa: BLE001
            log.warning("console.housekeeping_error", error=str(e)[:200])
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.pool()
    if not await db.schema_ready():
        raise RuntimeError("schema not ready — run `jpd db migrate`")

    _stop.clear()
    app.state.tasks = [asyncio.create_task(_poll_loop()),
                       asyncio.create_task(_housekeeping_loop())]
    CONSOLE_UP.set(1)
    log.info("console.startup", telegram_configured=TelegramClient().configured)
    yield

    _stop.set()
    for t in app.state.tasks:
        t.cancel()
    await asyncio.gather(*app.state.tasks, return_exceptions=True)
    CONSOLE_UP.set(0)
    await db.close()


app = FastAPI(title="jarvis-console", version=settings.version, lifespan=lifespan)


@app.get("/health")
async def health():
    return {"ok": True, "service": "jarvis-console", "version": settings.version}


@app.get("/ready")
async def ready():
    try:
        await db.fetchval("SELECT 1")
    except Exception as e:                                       # noqa: BLE001
        raise HTTPException(503, f"database unreachable: {type(e).__name__}")
    return {"ready": True, "telegram_configured": TelegramClient().configured}


@app.get("/", include_in_schema=False)
@app.get("/ui")
async def ui():
    """The operator dashboard — every aspect of the platform on one page.

    Served from the CONSOLE, not core, for the same reason the alert webhook is:
    a dashboard that goes dark whenever the thing it monitors goes dark is not a
    monitoring surface. The console survives a core outage (C7), so this page
    does too.

    Read-only by construction — see `dashboard.py`.
    """
    from fastapi.responses import HTMLResponse
    from .console import dashboard
    try:
        return HTMLResponse(await dashboard.page())
    except Exception as e:                                       # noqa: BLE001
        log.error("console.dashboard_failed", error=str(e)[:300])
        raise HTTPException(500, f"dashboard failed: {type(e).__name__}: {e}")


@app.get("/artifact/{artifact_id}")
async def artifact(artifact_id: int, raw: int = 0):
    """Serve one product for reading. READ-ONLY, and NOT a delivery channel.

    🔴 This is not how a buyer gets an artifact. Buyer delivery goes through
    `delivery.mint()`, which issues a hashed, expiring, download-counted token
    and REFUSES to mint one if the file is absent. This route has none of that
    and must never grow it — it exists so an operator reading the dashboard can
    click through to the thing being described.
    """
    from pathlib import Path

    from fastapi.responses import HTMLResponse, PlainTextResponse
    row = await db.fetchrow(
        "SELECT storage_uri, tier, need_id, words FROM artifacts WHERE id=$1",
        artifact_id)
    if row is None:
        raise HTTPException(404, f"no artifact {artifact_id}")
    path = Path((row["storage_uri"] or "").replace("file://", ""))
    if not path.is_file():
        # The artifact row outliving its file is a real failure mode the
        # commerce path guards against; say so plainly rather than 500.
        raise HTTPException(410, f"artifact {artifact_id} has no file on disk")
    text = path.read_text(errors="replace")
    if raw:
        return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")
    import html as _h
    return HTMLResponse(
        f"<!doctype html><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{_h.escape(row['tier'])} · need {row['need_id']}</title>"
        f"<style>body{{background:#fcfcfb;color:#0b0b0b;margin:0;"
        f"font:15px/1.65 ui-sans-serif,system-ui,sans-serif}}"
        f"@media(prefers-color-scheme:dark){{body{{background:#1a1a19;color:#fff}}}}"
        f"main{{max-width:780px;margin:0 auto;padding:28px 20px 80px}}"
        f"pre{{white-space:pre-wrap;word-wrap:break-word;font:inherit}}"
        f"a{{color:#2a78d6}}</style>"
        f"<main><p><a href='/ui'>← dashboard</a> · "
        f"<a href='/artifact/{artifact_id}?raw=1'>raw markdown</a> · "
        f"{row['words']:,} words</p><pre>{_h.escape(text)}</pre></main>")


@app.get("/tasks")
async def open_tasks():
    """The blocking queue. Readable even when core is completely down —
    that is the entire point of this service being separate."""
    return {"open": await tasks.open_tasks()}


@app.get("/streams")
async def streams():
    rows = await stream_status()
    configured = sum(1 for r in rows if r["chat_id"] is not None)
    return {"streams": rows, "configured": configured,
            "ht_001_complete": configured == len(rows) and configured > 0}


@app.post("/alerts")
async def receive_alerts(payload: dict):
    """Alertmanager webhook → a card in #alerts.

    The console owns this rather than core, on purpose: alerts about core being
    down must not be delivered *by* core. That is the same reasoning that makes
    the console a separate service at all.

    If Telegram is dormant the alert is logged at error level and the request
    still succeeds — returning 5xx would make Alertmanager retry forever
    against a channel that cannot accept it, and the log line is the fallback
    record either way.
    """
    from .console import cards
    from .console.telegram import TelegramClient

    alerts = payload.get("alerts") or []
    status = payload.get("status", "firing")
    client = TelegramClient()
    posted = 0

    for a in alerts:
        labels = a.get("labels") or {}
        ann = a.get("annotations") or {}
        name = labels.get("alertname", "unknown")
        sev = labels.get("severity", "warning")
        detail = ann.get("summary", "")
        if ann.get("rationale"):
            detail += f"\n\nWhy this exists: {ann['rationale']}"
        title = f"{'RESOLVED — ' if status == 'resolved' else ''}{name}"

        ALERTS_RECEIVED.labels(severity=sev, status=status).inc()
        log.warning("console.alert", alertname=name, severity=sev, status=status,
                    summary=detail[:200])
        try:
            await client.post("alerts", cards.alert(
                title=title, detail=detail,
                severity="info" if status == "resolved" else sev))
            posted += 1
        except Exception as e:                                   # noqa: BLE001
            log.error("console.alert_not_posted", alertname=name, error=str(e)[:200],
                      hint="the alert is in the logs but nobody was told")

    return {"received": len(alerts), "posted": posted}


@app.get("/metrics")
async def metrics():
    await _refresh_gauges()
    return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)


async def _refresh_gauges() -> None:
    """Age buckets, not just a count.

    'Three tasks open' is not actionable; 'one task open for four days' is. The
    architecture names `human_tasks_open{age_bucket}` a first-class metric
    precisely because a growing queue means the design is wrong and you need to
    be able to see it.
    """
    try:
        buckets = await db.fetch(
            """
            SELECT CASE
                     WHEN now() - created_at < interval '1 hour'  THEN 'lt_1h'
                     WHEN now() - created_at < interval '1 day'   THEN 'lt_1d'
                     WHEN now() - created_at < interval '3 days'  THEN 'lt_3d'
                     ELSE 'gte_3d' END AS bucket,
                   count(*) AS n
              FROM human_tasks WHERE status='open' GROUP BY 1
            """)
        got = {b["bucket"]: int(b["n"]) for b in buckets}
        for name in ("lt_1h", "lt_1d", "lt_3d", "gte_3d"):
            TASKS_OPEN.labels(age_bucket=name).set(got.get(name, 0))

        TASKS_UNANNOUNCED.set(int(await db.fetchval(
            "SELECT count(*) FROM human_tasks WHERE status='open' "
            "AND telegram_message_id IS NULL") or 0))
        TASKS_EXPIRED.set(int(await db.fetchval(
            "SELECT count(*) FROM human_tasks WHERE status='expired'") or 0))
        STREAMS_CONFIGURED.set(int(await db.fetchval(
            "SELECT count(*) FROM telegram_streams WHERE chat_id IS NOT NULL") or 0))

        # AlertNeverTripped's input. A rule that has never been verified gets a
        # deliberately huge age so it trips immediately rather than looking
        # fresh — "never run" must be worse than "run 11 days ago", not better.
        for r in await db.fetch(
                "SELECT alert_name, last_result, "
                "COALESCE(EXTRACT(EPOCH FROM (now() - last_tripped_at)) / 86400, 9999) "
                "AS age_days FROM alert_synthetics"):
            SYNTHETIC_AGE.labels(alert=r["alert_name"]).set(float(r["age_days"]))
            SYNTHETIC_RESULT.labels(alert=r["alert_name"]).set(
                1 if r["last_result"] == "fired" else 0)
    except Exception as e:                                       # noqa: BLE001
        log.warning("console.metrics_refresh_failed", error=str(e))
