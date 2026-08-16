"""GoHighLevel payment provider — DEC-002.

Same tenant, same Stripe account (GHL owns the Stripe connection; we hold no
Stripe key of our own), **new store** for JPD.

────────────────────────────────────────────────────────────────────────────
VERIFIED FROM THIS HOST, 2026-08-07 — facts, not assumptions
────────────────────────────────────────────────────────────────────────────
· `GET /products/` → 200, 53 products. The tenant is **co-tenanted** with an
  unrelated business; most of those products are not ours.
· Store membership IS readable and settable via `excludedStoreIds` on the
  product object. Two stores exist today: 43 products exclude one, 9 exclude
  the other, 1 excludes both. **This is the filterable namespace DEC-002 asked
  for** — it does not require guessing at product names.
· There is **no stores API**: `/store/store`, `/stores`, `/store/store/list`
  all 404. Creating the JPD store is therefore a browser task (HT-005).
· A live product page renders a real Stripe checkout (16 `STRIPE` references,
  "Add to Cart", "Buy now"). **The money path on this tenant works.**
· `GET /payments/integrations/provider/whitelabel` returns `providers: []`.
  ⚠️ This is NOT evidence that payments are disconnected — it lists *whitelabel*
  providers only. An earlier session drew exactly that false conclusion. Judge
  the payment path by the rendered checkout, not by this endpoint.
· Requests must go out over httpx. urllib's user-agent trips Cloudflare
  error 1010 on this API and every call 403s.

────────────────────────────────────────────────────────────────────────────
KNOWN TRAPS, carried from Pimlico — each cost real time
────────────────────────────────────────────────────────────────────────────
· **Price is in EUROS, not cents.** Sending cents lists everything at 100×.
  We hold integers-in-minor-units internally and convert at exactly one place
  (`_to_provider_amount`), then READ THE PRICE BACK and refuse on mismatch.
· The price endpoint is `/products/{id}/price` — **singular**. Plural 404s.
· `PUT /products/{id}` is a **REPLACE**: a partial payload blanks name/store.
· The product **LIST omits `medias`**; only a single GET is authoritative.
· A `sku` in the product payload is silently accepted and silently dropped.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from decimal import Decimal
from typing import Any, Optional

import httpx
import structlog

from ...connectors.base import ProbeResult, TestResult
from .base import (ParsedOrder, ProviderError, ProviderOffer, SignatureResult,
                   UnparseableWebhook)

log = structlog.get_logger("commerce.ghl")

GHL_BASE = "https://services.leadconnectorhq.com"
API_VERSION = "2021-07-28"

# Shared-token headers. GHL's webhook is a workflow action with a custom
# header — that is the strongest auth the platform offers here. It is a bearer
# secret, so it is compared with hmac.compare_digest and never logged.
_TOKEN_HEADERS = ("x-jpd-webhook-secret", "x-webhook-secret", "x-webhook-token")
_HMAC_HEADERS = ("x-hub-signature-256", "x-signature", "x-wh-signature")


class GHLProvider:
    name = "ghl_payments"
    kind = "api"

    def __init__(self, api_key: str = "", location_id: str = "",
                 webhook_secret: str = "", store_id: str = "",
                 checkout_base: str = ""):
        self.api_key = api_key or os.environ.get("JPD_GHL_API_KEY", "")
        self.location_id = location_id or os.environ.get("JPD_GHL_LOCATION_ID", "")
        self.webhook_secret = webhook_secret or os.environ.get("JPD_GHL_WEBHOOK_SECRET", "")
        self.store_id = store_id or os.environ.get("JPD_GHL_STORE_ID", "")
        # Checkout lives at the domain ROOT and is product-specific. The
        # store-slug forms 404 — verified in Pimlico.
        self.checkout_base = (checkout_base
                              or os.environ.get("JPD_GHL_CHECKOUT_BASE", "")).rstrip("/")

    # -- configuration -----------------------------------------------------
    @property
    def configured(self) -> bool:
        """A placeholder credential means DORMANT, never a 401 loop."""
        return all(v and v != "CHANGE_ME"
                   for v in (self.api_key, self.location_id))

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}",
                "Version": API_VERSION,
                "Content-Type": "application/json"}

    def _client(self) -> httpx.AsyncClient:
        # httpx, not urllib: urllib's UA trips Cloudflare 1010 here.
        return httpx.AsyncClient(base_url=GHL_BASE, headers=self._headers(), timeout=30)

    # -- store scoping -----------------------------------------------------
    async def _other_store_ids(self, c: httpx.AsyncClient, ours: str) -> list[str]:
        """Every store in this tenant EXCEPT ours, discovered from products.

        There is no stores API to ask, so the set of stores is recovered from
        the union of every product's `excludedStoreIds` — the one place GHL
        exposes membership. Measured 2026-08-09 on location
        `Zf5YnZeLjmXO7HrU6OUp`: 53 products yield exactly two store ids,
        `E80QUWhL04Xn8avZ3zwi` (43 products — an unrelated hiking/tours
        business) and `XnQcO1I5FSsEJ8Q2if6A` (9 — Pimlico's).

        Self-maintaining on purpose: a store added later is discovered and
        excluded automatically, rather than needing a config edit nobody
        remembers to make.

        FAILS CLOSED. If discovery cannot run, this raises rather than return an
        empty list — an empty list means "exclude nothing", which is precisely
        the unscoped POST this exists to replace.
        """
        try:
            r = await c.get("/products/",
                            params={"locationId": self.location_id, "limit": 100})
        except Exception as e:                                   # noqa: BLE001
            raise ProviderError(
                f"cannot enumerate stores to exclude ({type(e).__name__}); refusing "
                f"to create a product with no store scoping") from None
        if r.status_code != 200:
            raise ProviderError(
                f"cannot enumerate stores to exclude (GET /products/ -> "
                f"{r.status_code}); refusing to create a product with no store scoping")

        body = r.json()
        products = body.get("products") or body.get("data") or []
        seen: set[str] = set()
        for p in products:
            seen.update(p.get("excludedStoreIds") or [])
        seen.discard(ours)
        return sorted(seen)

    # -- connector contract ------------------------------------------------
    async def probe(self) -> ProbeResult:
        if not self.configured:
            return ProbeResult(ok=False, detail="credentials absent or CHANGE_ME")
        try:
            async with self._client() as c:
                r = await c.get("/products/",
                                params={"locationId": self.location_id, "limit": 1})
            return ProbeResult(ok=r.status_code == 200,
                               detail=f"GET /products/ -> {r.status_code}")
        except Exception as e:                                   # noqa: BLE001
            return ProbeResult(ok=False, detail=f"{type(e).__name__}: {e}")

    async def contract_test(self) -> TestResult:
        """Does the response have the SHAPE we parse?

        Checks the fields we actually depend on, not merely that a 200 came
        back — a renamed field returns 200 and yields plausible zeros, which is
        the failure mode that hides for weeks.
        """
        if not self.configured:
            return TestResult(ok=False, detail="credentials absent or CHANGE_ME")
        if not self.store_id or self.store_id == "CHANGE_ME":
            return TestResult(
                ok=False,
                detail="JPD_GHL_STORE_ID is not set — HT-005 (create the JPD store) "
                       "is outstanding. Without it, offers cannot be created in a "
                       "filterable namespace and would land in a co-tenanted catalogue.")
        try:
            async with self._client() as c:
                r = await c.get("/products/",
                                params={"locationId": self.location_id, "limit": 5})
            if r.status_code != 200:
                return TestResult(ok=False, detail=f"GET /products/ -> {r.status_code}")
            body = r.json()
            if "products" not in body or not isinstance(body["products"], list):
                return TestResult(ok=False,
                                  detail=f"response has no 'products' list; keys={list(body)[:8]}")
            required = {"_id", "name", "locationId"}
            for p in body["products"][:1]:
                missing = required - set(p)
                if missing:
                    return TestResult(
                        ok=False,
                        detail=f"product object is missing {sorted(missing)} — the "
                               f"shape we parse has changed",
                        observed_shape={"keys": sorted(p)[:20]})
            return TestResult(ok=True, detail=f"shape ok, {len(body['products'])} products",
                              observed_shape={"count": len(body["products"])})
        except Exception as e:                                   # noqa: BLE001
            return TestResult(ok=False, detail=f"{type(e).__name__}: {e}")

    # -- money conversion, in exactly one place ----------------------------
    @staticmethod
    def _to_provider_amount(price_minor: int) -> float:
        """Minor units → what GHL wants.

        🔴 GHL's price API takes **EUROS**, not cents. Pimlico sent cents and
        listed six products at 100× their intended price; it went unnoticed
        through create, read and display, and was caught only by reading the
        value back. This function is the single conversion boundary in the
        codebase — grep for it before changing anything about money.
        """
        if not isinstance(price_minor, int) or isinstance(price_minor, bool):
            raise ProviderError(
                f"price_minor must be int minor units, got {type(price_minor).__name__}")
        return float(Decimal(price_minor) / 100)

    @staticmethod
    def _from_provider_amount(amount: Any) -> int:
        """What GHL reports → minor units, as an integer."""
        try:
            return int((Decimal(str(amount)) * 100).to_integral_value())
        except Exception as e:                                   # noqa: BLE001
            raise UnparseableWebhook(f"amount {amount!r} is not a number: {e}") from None

    # -- offers ------------------------------------------------------------
    async def create_offer(self, *, name: str, description: str, tier: str,
                           price_minor: int, currency: str,
                           store_id: Optional[str] = None) -> ProviderOffer:
        if not self.configured:
            raise ProviderError("ghl_payments is not configured")
        store = store_id or self.store_id
        if not store:
            raise ProviderError(
                "no store_id — refusing to create an offer in a co-tenanted catalogue "
                "with no reliable filter (HT-005)")

        async with self._client() as c:
            # 🔴 EXCLUDE EVERY OTHER STORE, EXPLICITLY.
            #
            # Until 2026-08-09 this POST sent no store scoping at all: store_id
            # was recorded on the returned ProviderOffer and never transmitted.
            # A JPD product therefore landed in the shared catalogue beside the
            # 43 hiking-trail products of the unrelated business this tenant is
            # co-tenanted with, and the store id was a label we kept rather than
            # a filter the provider enforced.
            #
            # GHL exposes NO stores API — `/store/store`, `/stores/`,
            # `/store/store/list` and `/store/store/{id}` all 404, re-verified
            # today — and `?storeId=` on /products/ is silently IGNORED (a
            # deliberately fake id returns the same 53 products). Membership is
            # only expressible the other way round: a product lists the stores
            # it is NOT in.
            others = await self._other_store_ids(c, store)
            r = await c.post("/products/", json={
                "name": name, "description": description,
                "productType": "DIGITAL", "locationId": self.location_id,
                "availableInStore": True,
                "excludedStoreIds": others,
            })
            if r.status_code not in (200, 201):
                raise ProviderError(f"create product -> {r.status_code}: {r.text[:200]}")
            product = r.json().get("product") or r.json()
            pid = product.get("_id") or product.get("id")
            if not pid:
                raise ProviderError(f"created product has no id; keys={list(product)[:10]}")

            # NOTE: '/price' is SINGULAR. The plural form 404s.
            amount = self._to_provider_amount(price_minor)
            rp = await c.post(f"/products/{pid}/price", json={
                "name": tier, "type": "one_time",
                "currency": currency, "amount": amount,
                "locationId": self.location_id,
            })
            if rp.status_code not in (200, 201):
                raise ProviderError(f"create price -> {rp.status_code}: {rp.text[:200]}")

            # 🔴 READ-BACK GUARD. The API accepted our number; that is not the
            # same as having stored the number we meant. This is the check that
            # would have caught the 100x bug on day one instead of after six
            # products were live.
            rb = await c.get(f"/products/{pid}/price", params={"locationId": self.location_id})
            if rb.status_code == 200:
                got = rb.json()
                prices = got.get("prices") or ([got] if got.get("amount") is not None else [])
                if prices:
                    stored_minor = self._from_provider_amount(prices[0].get("amount"))
                    if stored_minor != price_minor:
                        raise ProviderError(
                            f"PRICE READ-BACK MISMATCH: sent {price_minor} minor "
                            f"({amount} major), provider stored {stored_minor} minor. "
                            f"Refusing to publish an offer at the wrong price.")

            # 🔴 STORE READ-BACK GUARD. Same reasoning as the price read-back
            # above: the API accepting `excludedStoreIds` is not the same as
            # having stored it. GHL silently ignores `?storeId=` on /products/,
            # so it demonstrably accepts store parameters it does not honour —
            # which is exactly the shape of failure that hides for weeks.
            #
            # A product that is NOT excluded from the hiking-tours store is
            # visible in the hiking-tours store. Refuse the offer rather than
            # publish into a stranger's catalogue.
            if others:
                rs = await c.get(f"/products/{pid}",
                                 params={"locationId": self.location_id})
                if rs.status_code == 200:
                    got = rs.json().get("product") or rs.json()
                    stored = set(got.get("excludedStoreIds") or [])
                    missing = [s for s in others if s not in stored]
                    if missing:
                        raise ProviderError(
                            f"STORE SCOPING READ-BACK MISMATCH: product {pid} is NOT "
                            f"excluded from {missing}. It would be visible in "
                            f"{'that store' if len(missing) == 1 else 'those stores'} "
                            f"in a co-tenanted catalogue. Refusing to publish.")
                else:
                    raise ProviderError(
                        f"cannot read back product {pid} to confirm store scoping "
                        f"(GET -> {rs.status_code}); refusing to publish unverified")

        checkout = f"{self.checkout_base}/product-details/product/{pid}" \
            if self.checkout_base else ""
        log.info("ghl.offer_created", product_id=str(pid), tier=tier,
                 store_id=store, excluded_stores=len(others))
        return ProviderOffer(external_ref=str(pid), checkout_url=checkout, store_id=store)

    # -- webhooks ----------------------------------------------------------
    def verify_webhook(self, raw: bytes, headers: dict[str, str]) -> SignatureResult:
        secret = self.webhook_secret
        if not secret or secret == "CHANGE_ME":
            # The safe direction. Pimlico returned VALID when unconfigured so
            # that verification could be introduced without risk to a live
            # payment path — a sensible migration for an existing system, and
            # the wrong default for a new one.
            return SignatureResult(False, "unconfigured — refusing to accept unverified money")

        h = {k.lower(): v for k, v in headers.items()}
        for name in _TOKEN_HEADERS:
            if name in h:
                ok = hmac.compare_digest(h[name], secret)
                return SignatureResult(ok, ("token_ok:" if ok else "token_mismatch:") + name)
        for name in _HMAC_HEADERS:
            if name in h:
                provided = h[name].split("=", 1)[-1].strip()
                digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
                ok = hmac.compare_digest(provided, digest)
                return SignatureResult(ok, ("hmac_ok:" if ok else "hmac_mismatch:") + name)
        return SignatureResult(False, "no_signature_header")

    def parse_order(self, payload: dict[str, Any]) -> ParsedOrder:
        """Raise on anything we cannot fully understand. No defaults."""
        if not isinstance(payload, dict):
            raise UnparseableWebhook(f"payload is {type(payload).__name__}, expected object")

        custom = payload.get("customFields") or {}
        external_ref = (payload.get("productId") or custom.get("product_id")
                        or payload.get("product_id") or "")
        provider_ref = (payload.get("transactionId") or payload.get("orderId")
                        or payload.get("id") or "")
        buyer_ref = (payload.get("contactId") or payload.get("contact_id")
                     or payload.get("email") or "")
        amount_raw = payload.get("amount", payload.get("total"))
        if amount_raw is None:
            raise UnparseableWebhook("no 'amount' in payload — absent is not zero")

        return ParsedOrder(
            provider=self.name,
            provider_ref=str(provider_ref),
            external_ref=str(external_ref),
            amount_minor=self._from_provider_amount(amount_raw),
            currency=str(payload.get("currency") or "EUR").upper(),
            buyer_ref=str(buyer_ref),
            buyer_email=payload.get("email") or None,
            event_type=str(payload.get("type") or "payment"),
            raw=payload,
        )
