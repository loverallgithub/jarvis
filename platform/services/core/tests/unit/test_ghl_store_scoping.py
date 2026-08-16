"""A JPD product must land in the JPD store, and nowhere else.

This tenant is CO-TENANTED. Measured 2026-08-09 on location
`Zf5YnZeLjmXO7HrU6OUp`: 53 products across two stores —
`E80QUWhL04Xn8avZ3zwi` (43 products: Drenthepad, Pieterpad, castle tours — an
unrelated hiking/tours business) and `XnQcO1I5FSsEJ8Q2if6A` (9: Pimlico's).

Until today `create_offer` sent NO store scoping at all. `store_id` was recorded
on the returned ProviderOffer and never transmitted, so a JPD product would have
been created into the shared catalogue beside the hiking trails, and the store id
was a label we kept rather than a filter GHL enforced.

Two API facts make the read-back guard non-optional:
  · there is no stores API — /store/store, /stores/, /store/store/list and
    /store/store/{id} all 404
  · `?storeId=` on /products/ is SILENTLY IGNORED — a deliberately fake id
    returns the same 53 products

So GHL demonstrably accepts store parameters it does not honour. Accepting the
POST is not evidence the exclusion was stored.
"""
from __future__ import annotations

import json

import httpx
import pytest

from jarvis.commerce.providers.base import ProviderError
from jarvis.commerce.providers.ghl import GHLProvider

OURS = "PhxzRjZIIfHmC06vqj8b"
HIKING = "E80QUWhL04Xn8avZ3zwi"
PIMLICO = "XnQcO1I5FSsEJ8Q2if6A"


def _provider() -> GHLProvider:
    return GHLProvider(api_key="k", location_id="Zf5YnZeLjmXO7HrU6OUp",
                       store_id=OURS, checkout_base="https://nevasca.pro")


def _mock(*, listing=None, created_excludes=None, readback_status=200,
          posted=None):
    """A GHL stand-in. `posted` collects the create payload for assertions."""
    listing = listing if listing is not None else [
        {"_id": "p1", "excludedStoreIds": [HIKING]},
        {"_id": "p2", "excludedStoreIds": [PIMLICO]},
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if method == "GET" and path == "/products/":
            return httpx.Response(200, json={"products": listing})
        if method == "POST" and path == "/products/":
            body = json.loads(request.content)
            if posted is not None:
                posted.append(body)
            return httpx.Response(201, json={"product": {"_id": "new1"}})
        if method == "POST" and path.endswith("/price"):
            return httpx.Response(201, json={"amount": 99.0})
        if method == "GET" and path.endswith("/price"):
            return httpx.Response(200, json={"prices": [{"amount": 99.0}]})
        if method == "GET" and path == "/products/new1":
            if readback_status != 200:
                return httpx.Response(readback_status, json={})
            ex = created_excludes if created_excludes is not None else [HIKING, PIMLICO]
            return httpx.Response(200, json={"product": {"_id": "new1",
                                                         "excludedStoreIds": ex}})
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


@pytest.fixture
def patched(monkeypatch):
    def use(transport):
        def _client(self):
            return httpx.AsyncClient(base_url="https://services.leadconnectorhq.com",
                                     transport=transport, headers={})
        monkeypatch.setattr(GHLProvider, "_client", _client)
    return use


# ── discovery ──────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_every_other_store_is_discovered_and_ours_is_not(patched):
    patched(_mock())
    p = _provider()
    async with p._client() as c:
        others = await p._other_store_ids(c, OURS)
    assert others == sorted([HIKING, PIMLICO])
    assert OURS not in others


@pytest.mark.asyncio
async def test_our_own_store_is_never_excluded(patched):
    """Excluding ourselves would put the product in no store at all."""
    patched(_mock(listing=[{"_id": "p1", "excludedStoreIds": [HIKING, OURS]}]))
    p = _provider()
    async with p._client() as c:
        assert OURS not in await p._other_store_ids(c, OURS)


@pytest.mark.asyncio
async def test_discovery_failure_RAISES_rather_than_excluding_nothing(patched):
    """An empty list means 'exclude nothing' — the unscoped POST this replaces.
    Failing closed is the whole point."""
    async def broken(request):
        return httpx.Response(500, json={})
    patched(httpx.MockTransport(broken))
    p = _provider()
    async with p._client() as c:
        with pytest.raises(ProviderError, match="no store scoping"):
            await p._other_store_ids(c, OURS)


# ── the POST ───────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_the_create_payload_actually_carries_excludedStoreIds(patched):
    """The regression that started this: store_id was recorded and never sent."""
    posted: list = []
    patched(_mock(posted=posted))
    await _provider().create_offer(name="n", description="d", tier="roadmap",
                                   price_minor=9900, currency="EUR")
    assert posted, "no product was created"
    assert set(posted[0]["excludedStoreIds"]) == {HIKING, PIMLICO}
    assert OURS not in posted[0]["excludedStoreIds"]


@pytest.mark.asyncio
async def test_a_successful_offer_returns_the_store_and_a_checkout_url(patched):
    patched(_mock())
    offer = await _provider().create_offer(name="n", description="d", tier="roadmap",
                                           price_minor=9900, currency="EUR")
    assert offer.store_id == OURS
    assert offer.checkout_url.endswith("/product-details/product/new1")


# ── the read-back guard ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_an_ignored_exclusion_is_CAUGHT_not_published(patched):
    """GHL accepts store parameters it ignores — proven by ?storeId=. So the
    product coming back without our exclusions must fail the offer."""
    patched(_mock(created_excludes=[]))
    with pytest.raises(ProviderError, match="STORE SCOPING READ-BACK MISMATCH"):
        await _provider().create_offer(name="n", description="d", tier="roadmap",
                                       price_minor=9900, currency="EUR")


@pytest.mark.asyncio
async def test_a_partially_applied_exclusion_is_caught(patched):
    """Excluded from Pimlico's store but NOT the hiking store means it is
    visible beside the hiking trails."""
    patched(_mock(created_excludes=[PIMLICO]))
    with pytest.raises(ProviderError, match=HIKING):
        await _provider().create_offer(name="n", description="d", tier="roadmap",
                                       price_minor=9900, currency="EUR")


@pytest.mark.asyncio
async def test_an_unreadable_product_is_refused_rather_than_assumed_good(patched):
    patched(_mock(readback_status=500))
    with pytest.raises(ProviderError, match="refusing to publish unverified"):
        await _provider().create_offer(name="n", description="d", tier="roadmap",
                                       price_minor=9900, currency="EUR")


@pytest.mark.asyncio
async def test_a_single_store_tenant_needs_no_exclusions_and_no_readback(patched):
    """If ours is the only store, there is nothing to exclude — and demanding a
    read-back would fail an offer that is already correctly scoped."""
    patched(_mock(listing=[{"_id": "p1", "excludedStoreIds": []}]))
    offer = await _provider().create_offer(name="n", description="d", tier="roadmap",
                                           price_minor=9900, currency="EUR")
    assert offer.store_id == OURS


@pytest.mark.asyncio
async def test_no_store_id_still_refuses_outright(patched):
    patched(_mock())
    p = GHLProvider(api_key="k", location_id="loc", store_id="")
    with pytest.raises(ProviderError, match="no store_id"):
        await p.create_offer(name="n", description="d", tier="roadmap",
                             price_minor=9900, currency="EUR")
