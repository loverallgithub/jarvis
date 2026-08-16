"""A deterministic payment provider for journey tests.

This exists so the three buyer journeys and the upgrade can be exercised
end-to-end **before** the real store exists (HT-005), and re-exercised on every
commerce deploy without moving real money.

⚠️ It is a test double for the PROVIDER, not for our own money path. Everything
downstream of `parse_order` — signature enforcement, the amount check against
the offers row, idempotency, entitlement, artifact existence, token minting,
notification — is the real code. A stub that shortcut those would prove
nothing, which is the trap most payment test suites fall into.

It is registered only when JPD_ENABLE_STUB_PROVIDER is truthy, and the commerce
service refuses to start with it enabled while JPD_ENV=production.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from decimal import Decimal
from typing import Any, Optional

from ...connectors.base import ProbeResult, TestResult
from .base import ParsedOrder, ProviderOffer, SignatureResult, UnparseableWebhook

STUB_SECRET = "stub-webhook-secret"


class StubProvider:
    name = "stub"
    kind = "api"

    def __init__(self, secret: str = STUB_SECRET):
        self.secret = secret
        self._offers: dict[str, dict] = {}
        self._counter = 0

    async def probe(self) -> ProbeResult:
        return ProbeResult(ok=True, detail="stub is always reachable")

    async def contract_test(self) -> TestResult:
        return TestResult(ok=True, detail="stub shape is fixed by construction")

    async def create_offer(self, *, name: str, description: str, tier: str,
                           price_minor: int, currency: str,
                           store_id: Optional[str] = None) -> ProviderOffer:
        self._counter += 1
        ref = f"stub_prod_{self._counter:04d}"
        self._offers[ref] = {"name": name, "tier": tier, "price_minor": price_minor,
                             "currency": currency, "store_id": store_id}
        return ProviderOffer(external_ref=ref,
                             checkout_url=f"https://stub.invalid/checkout/{ref}",
                             store_id=store_id or "stub_store")

    def verify_webhook(self, raw: bytes, headers: dict[str, str]) -> SignatureResult:
        """Real HMAC. The stub must not be easier to satisfy than production —
        otherwise the journey tests would not exercise signature handling."""
        h = {k.lower(): v for k, v in headers.items()}
        provided = h.get("x-jpd-webhook-secret") or h.get("x-signature")
        if provided is None:
            return SignatureResult(False, "no_signature_header")
        if h.get("x-jpd-webhook-secret"):
            ok = hmac.compare_digest(provided, self.secret)
            return SignatureResult(ok, "token_ok" if ok else "token_mismatch")
        digest = hmac.new(self.secret.encode(), raw, hashlib.sha256).hexdigest()
        ok = hmac.compare_digest(provided.split("=", 1)[-1], digest)
        return SignatureResult(ok, "hmac_ok" if ok else "hmac_mismatch")

    def parse_order(self, payload: dict[str, Any]) -> ParsedOrder:
        if not isinstance(payload, dict):
            raise UnparseableWebhook(f"payload is {type(payload).__name__}")
        if "amount" not in payload:
            raise UnparseableWebhook("no 'amount' in payload — absent is not zero")
        try:
            amount_minor = int((Decimal(str(payload["amount"])) * 100).to_integral_value())
        except Exception:                                        # noqa: BLE001
            raise UnparseableWebhook(f"amount {payload['amount']!r} is not a number") from None

        return ParsedOrder(
            provider=self.name,
            provider_ref=str(payload.get("transactionId") or ""),
            external_ref=str(payload.get("productId") or ""),
            amount_minor=amount_minor,
            currency=str(payload.get("currency") or "EUR").upper(),
            buyer_ref=str(payload.get("contactId") or ""),
            buyer_email=payload.get("email"),
            event_type=str(payload.get("type") or "payment"),
            raw=payload,
        )

    # -- test helpers ------------------------------------------------------
    def sign(self, raw: bytes) -> dict[str, str]:
        return {"X-JPD-Webhook-Secret": self.secret}

    def payment_payload(self, *, external_ref: str, amount_minor: int,
                        provider_ref: str, buyer_ref: str = "buyer_1",
                        email: str = "buyer@example.test",
                        currency: str = "EUR") -> dict[str, Any]:
        """Build a payload shaped like the real thing — amount in MAJOR units,
        because that is what providers send and where the 100x bug lives."""
        return {"transactionId": provider_ref, "productId": external_ref,
                "amount": float(Decimal(amount_minor) / 100), "currency": currency,
                "contactId": buyer_ref, "email": email, "type": "payment"}


def stub_enabled() -> bool:
    return os.environ.get("JPD_ENABLE_STUB_PROVIDER", "").strip().lower() in (
        "1", "true", "yes", "on")
