"""Provider contract — parsing and money conversion.

The GHL tests here are pure: they exercise parsing, signature checking and the
euro/cent boundary without touching the network. The network-facing half is a
`contract_test()` that runs against the real API and feeds the dormancy state
machine — a unit test cannot tell you the provider renamed a field.
"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from jarvis.commerce.providers.base import ParsedOrder, UnparseableWebhook
from jarvis.commerce.providers.ghl import GHLProvider
from jarvis.commerce.providers.stub import StubProvider


@pytest.fixture
def ghl():
    return GHLProvider(api_key="k", location_id="loc", webhook_secret="s3cret",
                       store_id="store_1", checkout_base="https://shop.test")


# ---------------------------------------------------------------------------
# money conversion — the 100x bug
# ---------------------------------------------------------------------------

def test_ghl_price_is_sent_in_euros_not_cents(ghl):
    """🔴 GHL's price API takes EUROS. Pimlico sent cents and listed six
    products at 100x. One conversion boundary, tested."""
    assert ghl._to_provider_amount(29700) == 297.00
    assert ghl._to_provider_amount(100) == 1.00
    assert ghl._to_provider_amount(1) == 0.01


def test_the_conversion_round_trips(ghl):
    for minor in (1, 99, 100, 29700, 371300):
        assert ghl._from_provider_amount(ghl._to_provider_amount(minor)) == minor


def test_a_float_price_is_refused_at_the_boundary(ghl):
    from jarvis.commerce.providers.base import ProviderError
    with pytest.raises(ProviderError, match="minor units"):
        ghl._to_provider_amount(297.0)              # type: ignore[arg-type]


def test_a_non_numeric_amount_is_unparseable(ghl):
    with pytest.raises(UnparseableWebhook):
        ghl._from_provider_amount("free")


# ---------------------------------------------------------------------------
# parsing — absent is not zero
# ---------------------------------------------------------------------------

def test_a_missing_amount_raises_instead_of_defaulting_to_zero(ghl):
    """Pimlico's handler used `payload.get("amount", 0)`, so a payload with no
    amount produced a complete-looking order for €0."""
    with pytest.raises(UnparseableWebhook, match="absent is not zero"):
        ghl.parse_order({"transactionId": "t", "productId": "p", "contactId": "c"})


def test_missing_identifiers_raise_instead_of_defaulting(ghl):
    """`.get("product_id", "unknown")` turns a broken webhook into an order for
    a product literally named "unknown"."""
    with pytest.raises(UnparseableWebhook, match="external_ref"):
        ghl.parse_order({"transactionId": "t", "contactId": "c", "amount": 297})
    with pytest.raises(UnparseableWebhook, match="provider_ref"):
        ghl.parse_order({"productId": "p", "contactId": "c", "amount": 297})


def test_a_zero_or_negative_amount_is_refused(ghl):
    for amt in (0, -5):
        with pytest.raises(UnparseableWebhook, match="positive"):
            ghl.parse_order({"transactionId": "t", "productId": "p",
                             "contactId": "c", "amount": amt})


def test_the_parsed_order_carries_no_tier(ghl):
    """The tier comes from OUR offers row. Taking it from the payload would let
    the buyer choose what they receive — Pimlico read
    `customFields.tier` with a default of "starter"."""
    o = ghl.parse_order({"transactionId": "t", "productId": "p", "contactId": "c",
                         "amount": 297, "customFields": {"tier": "deployed"}})
    assert not hasattr(o, "tier")
    assert isinstance(o, ParsedOrder)
    assert o.amount_minor == 29700


def test_a_non_dict_payload_is_refused(ghl):
    for bad in ([], "string", None, 42):
        with pytest.raises(UnparseableWebhook):
            ghl.parse_order(bad)                     # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# signatures — enforced from day one
# ---------------------------------------------------------------------------

def test_an_unconfigured_secret_rejects_rather_than_accepts(ghl):
    """The safe direction. Pimlico returned VALID when unconfigured so
    verification could be introduced on a live payment path without risk — a
    sound migration for an existing system, and the wrong default for a new
    one."""
    p = GHLProvider(api_key="k", location_id="l", webhook_secret="")
    r = p.verify_webhook(b"{}", {})
    assert r.valid is False
    assert r.should_reject is True
    assert "unconfigured" in r.reason

    p2 = GHLProvider(api_key="k", location_id="l", webhook_secret="CHANGE_ME")
    assert p2.verify_webhook(b"{}", {}).should_reject is True


def test_a_matching_shared_token_is_accepted(ghl):
    r = ghl.verify_webhook(b"{}", {"X-JPD-Webhook-Secret": "s3cret"})
    assert r.valid is True and r.should_reject is False


def test_a_wrong_shared_token_is_rejected(ghl):
    assert ghl.verify_webhook(b"{}", {"X-JPD-Webhook-Secret": "wrong"}).should_reject


def test_a_valid_hmac_is_accepted(ghl):
    raw = b'{"amount": 297}'
    sig = hmac.new(b"s3cret", raw, hashlib.sha256).hexdigest()
    assert ghl.verify_webhook(raw, {"X-Signature": f"sha256={sig}"}).valid is True
    assert ghl.verify_webhook(raw, {"X-Signature": sig}).valid is True


def test_an_hmac_over_different_bytes_is_rejected(ghl):
    sig = hmac.new(b"s3cret", b'{"amount": 1}', hashlib.sha256).hexdigest()
    assert ghl.verify_webhook(b'{"amount": 297}', {"X-Signature": sig}).should_reject


def test_no_signature_header_is_rejected(ghl):
    r = ghl.verify_webhook(b"{}", {"content-type": "application/json"})
    assert r.should_reject and r.reason == "no_signature_header"


# ---------------------------------------------------------------------------
# the stub must not be easier to satisfy than production
# ---------------------------------------------------------------------------

def test_the_stub_enforces_real_signatures():
    """If the double accepted anything, the journey tests would not exercise
    signature handling at all."""
    s = StubProvider()
    raw = json.dumps({"amount": 1}).encode()
    assert s.verify_webhook(raw, {}).should_reject
    assert s.verify_webhook(raw, {"X-JPD-Webhook-Secret": "nope"}).should_reject
    assert s.verify_webhook(raw, s.sign(raw)).valid is True


def test_the_stub_sends_major_units_like_a_real_provider():
    """The payload carries 297.0, not 29700 — so the journey tests actually
    cross the euro/cent boundary."""
    s = StubProvider()
    p = s.payment_payload(external_ref="x", amount_minor=29700, provider_ref="t")
    assert p["amount"] == 297.00
    assert s.parse_order(p).amount_minor == 29700


def test_the_stub_refuses_a_missing_amount():
    s = StubProvider()
    with pytest.raises(UnparseableWebhook):
        s.parse_order({"transactionId": "t", "productId": "p", "contactId": "c"})


async def test_ghl_contract_test_fails_without_a_store_id():
    """HT-005 gate. Without a store, offers would land in a co-tenanted
    catalogue of 53 products with no reliable filter."""
    p = GHLProvider(api_key="k", location_id="l", store_id="")
    r = await p.contract_test()
    assert r.ok is False
    assert "HT-005" in r.detail


async def test_ghl_is_dormant_without_credentials():
    p = GHLProvider(api_key="", location_id="")
    assert p.configured is False
    assert (await p.probe()).ok is False
    assert (await p.contract_test()).ok is False
