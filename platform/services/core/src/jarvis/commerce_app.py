"""jarvis-commerce — the money path, deployed separately.

Same image as jarvis-core, different ASGI app and its own swarm service with
its own pinned version. That is the practical form of C5: **the money path is
not redeployed for feature work.** Rebuilding the image for a core change does
not touch this service until someone deliberately rolls it, and rolling it runs
the journey tests first.

Endpoints are deliberately few. Everything here either takes money or hands
over a product; nothing else belongs in this process.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

from . import db
from .commerce import delivery, fulfilment, notify, orders
from .commerce.delivery import ArtifactMissing, TokenInvalid
from .commerce.providers import base as providers
from .commerce.providers.ghl import GHLProvider
from .commerce.providers.stub import StubProvider, stub_enabled
from .config import settings

log = structlog.get_logger("commerce")

# --- metrics ---------------------------------------------------------------
# Business metrics on day one, not "later". A beautiful pipeline dashboard over
# a revenue path that cannot take money is a nicer view of zero.
ORDERS_TOTAL = Counter("jpd_orders_total", "Orders by outcome", ["outcome"])
REVENUE_MINOR = Counter("jpd_revenue_minor_total", "Revenue in minor units", ["currency"])
WEBHOOKS = Counter("jpd_webhooks_total", "Inbound webhooks", ["provider", "result"])
DOWNLOADS = Counter("jpd_downloads_total", "Artifact downloads", ["result"])
NOTIFICATIONS_OWED = Gauge("jpd_notifications_owed",
                           "Buyers who paid and have not been told (pending+failed+dormant)")
UNDELIVERED = Gauge("jpd_undelivered_paid_orders",
                    "Paid orders with a failed fulfilment — MUST be 0")
ARTIFACTS_MISSING = Gauge("jpd_artifacts_missing",
                          "Artifacts behind a live token whose file is absent")
COMMERCE_UP = Gauge("jpd_commerce_up", "1 when commerce has completed startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    providers.register(GHLProvider())

    if stub_enabled():
        # 🔴 A test double on a live money path would let anyone mint
        # entitlements with a known secret. Refusing to start is the only safe
        # response; a warning would be read once and then ignored forever.
        if settings.env == "production":
            raise RuntimeError(
                "JPD_ENABLE_STUB_PROVIDER is set while JPD_ENV=production. "
                "The stub accepts a publicly-known secret and would mint real "
                "entitlements. Refusing to start.")
        providers.register(StubProvider())
        log.warning("commerce.stub_enabled", env=settings.env)

    await db.pool()
    if not await db.schema_ready():
        raise RuntimeError("schema not ready — run `jpd db migrate`")

    COMMERCE_UP.set(1)
    log.info("commerce.startup", providers=providers.registered(), env=settings.env)
    yield
    COMMERCE_UP.set(0)
    await db.close()


app = FastAPI(title="jarvis-commerce", version=settings.version, lifespan=lifespan)


@app.get("/health")
async def health():
    return {"ok": True, "service": "jarvis-commerce", "version": settings.version}


@app.get("/ready")
async def ready():
    try:
        await db.fetchval("SELECT 1")
    except Exception as e:                                       # noqa: BLE001
        raise HTTPException(503, f"database unreachable: {type(e).__name__}")
    return {"ready": True, "providers": providers.registered()}


@app.post("/webhooks/{provider}/payment")
async def payment_webhook(provider: str, request: Request):
    """G1. Must be fast and must never fulfil an unverified payment.

    Note the response codes: a rejected-but-understood webhook returns 200 so
    the provider stops retrying a payload that will never be valid. Only a bad
    signature returns 401, because that IS worth retrying once the secret is
    fixed.
    """
    raw = await request.body()
    try:
        result = await orders.receive(provider, raw, dict(request.headers))
    except providers.ProviderError as e:
        WEBHOOKS.labels(provider=provider, result="unknown_provider").inc()
        raise HTTPException(404, str(e))

    WEBHOOKS.labels(provider=provider,
                    result="duplicate" if result.duplicate else
                           ("accepted" if result.accepted else "rejected")).inc()

    if not result.accepted:
        ORDERS_TOTAL.labels(outcome="rejected").inc()
        if result.http_status == 401:
            raise HTTPException(401, result.reason)
        return {"accepted": False, "reason": result.reason}

    if result.duplicate:
        return {"accepted": True, "duplicate": True, "order_id": result.order_id}

    ORDERS_TOTAL.labels(outcome="accepted").inc()
    order = await db.fetchrow(
        "SELECT amount_minor, currency FROM orders WHERE id = $1", result.order_id)
    if order:
        REVENUE_MINOR.labels(currency=order["currency"]).inc(int(order["amount_minor"]))

    # G3 + G4 inline. Fulfilment is fast (a stat and a token) and doing it here
    # means a buyer who paid has their links before the request returns.
    fr = await fulfilment.fulfil(result.entitlement_id)
    if fr.ok:
        await db.execute("UPDATE orders SET status='fulfilled' WHERE id=$1", result.order_id)
    await notify.send_delivery(result.entitlement_id, result.order_id, fr.delivered)

    return {"accepted": True, "order_id": result.order_id,
            "entitlement_id": result.entitlement_id,
            "fulfilment": fr.status,
            "delivered_tiers": [d["tier"] for d in fr.delivered],
            "failed": fr.failed}


@app.get("/download/{token}")
async def download(token: str):
    """Hand over the artifact. One undifferentiated failure for every reason a
    token can be invalid — telling a guesser which guess was once real is a
    gift."""
    try:
        path, meta = await delivery.redeem(token)
    except TokenInvalid:
        DOWNLOADS.labels(result="invalid").inc()
        raise HTTPException(404, "not found")
    except ArtifactMissing as e:
        # The buyer is entitled and the file is gone. This is OUR failure, and
        # it must be loud: a 404 here would look like a bad link.
        DOWNLOADS.labels(result="artifact_missing").inc()
        log.error("download.artifact_missing", detail=str(e))
        raise HTTPException(503, "this file is temporarily unavailable; we have been alerted")

    DOWNLOADS.labels(result="ok").inc()
    return FileResponse(path, filename=path.name)


@app.get("/metrics")
async def metrics():
    await _refresh_gauges()
    return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)


async def _refresh_gauges() -> None:
    try:
        owed = await notify.pending_and_failed()
        NOTIFICATIONS_OWED.set(owed.get("owed", 0))
        UNDELIVERED.set(len(await fulfilment.undelivered_paid_orders()))
        ARTIFACTS_MISSING.set(int(await db.fetchval(
            "SELECT count(DISTINCT a.id) FROM artifacts a "
            "JOIN delivery_tokens t ON t.artifact_id = a.id "
            "WHERE a.missing_since IS NOT NULL AND t.revoked_at IS NULL") or 0))
    except Exception as e:                                       # noqa: BLE001
        log.warning("commerce.metrics_refresh_failed", error=str(e))
