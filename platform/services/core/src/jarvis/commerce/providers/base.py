"""Payment provider contract.

A payment provider is a Connector (`connectors/base.py`) with three extra
obligations: it can create an offer, it can verify that an inbound webhook
really came from it, and it can parse that webhook into a **typed order**.

────────────────────────────────────────────────────────────────────────────
WHY `parse_order` RAISES INSTEAD OF RETURNING A PARTIAL DICT
────────────────────────────────────────────────────────────────────────────
Pimlico's GHL payment handler built its order from `payload.get(...)` chains
with defaults:

    "product_id": payload.get("customFields", {}).get("product_id", "unknown"),
    "tier":       payload.get("customFields", {}).get("tier", "starter"),
    "amount":     payload.get("amount", 0),

A webhook missing every field it needed therefore produced a *complete-looking*
order for product "unknown", tier "starter", amount 0 — and the code downstream
treated `amount > 0` as proof of payment, so a payload claiming `amount: 1`
would have minted a €297 product. Nothing in that path could ever report a
malformed webhook, because malformed and valid had the same type.

Here a payload that cannot be parsed raises `UnparseableWebhook`, the raw body
is written to `provider_events`, and no order exists. Absent is not zero.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from ...connectors.base import ProbeResult, TestResult


class UnparseableWebhook(ValueError):
    """The payload is not an order we can act on. Quarantine it; do not guess."""


class ProviderError(RuntimeError):
    """The provider could not complete an operation. Raised, never returned."""


@dataclass(frozen=True)
class SignatureResult:
    valid: bool
    reason: str

    @property
    def should_reject(self) -> bool:
        """JPD enforces from day one.

        Pimlico's verifier deliberately shipped in *observe* mode, returning
        `(True, "unconfigured")` when no secret was set — the unsafe direction,
        chosen so verification could be rolled onto a live payment path without
        risk. That was a reasonable migration strategy for an existing system.
        A new system has no such excuse: no valid signature, no order.
        """
        return not self.valid


@dataclass(frozen=True)
class ParsedOrder:
    """A webhook, understood. Every field here is REQUIRED.

    Note what is deliberately absent: no `tier`. The tier is whatever the
    `offers` row for this `external_ref` says it is — taking it from the
    payload would let the caller choose what they bought.
    """
    provider: str
    provider_ref: str          # the provider's unique id for this payment
    external_ref: str          # the provider's id for the thing purchased
    amount_minor: int          # MINOR units, integer, as reported by the provider
    currency: str
    buyer_ref: str
    buyer_email: Optional[str] = None
    event_type: str = "payment"
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("provider", "provider_ref", "external_ref", "buyer_ref", "currency"):
            if not str(getattr(self, name) or "").strip():
                raise UnparseableWebhook(f"missing required field {name!r}")
        if not isinstance(self.amount_minor, int) or isinstance(self.amount_minor, bool):
            raise UnparseableWebhook(
                f"amount_minor must be an int in minor units, "
                f"got {type(self.amount_minor).__name__}")
        if self.amount_minor <= 0:
            raise UnparseableWebhook(f"amount_minor must be positive, got {self.amount_minor}")


@dataclass(frozen=True)
class ProviderOffer:
    """What the provider gives back when an offer is created there."""
    external_ref: str
    checkout_url: str
    store_id: Optional[str] = None


@runtime_checkable
class PaymentProvider(Protocol):
    name: str
    kind: str

    async def probe(self) -> ProbeResult: ...
    async def contract_test(self) -> TestResult: ...

    async def create_offer(self, *, name: str, description: str, tier: str,
                           price_minor: int, currency: str,
                           store_id: Optional[str]) -> ProviderOffer: ...

    def verify_webhook(self, raw: bytes, headers: dict[str, str]) -> SignatureResult: ...

    def parse_order(self, payload: dict[str, Any]) -> ParsedOrder: ...


_REGISTRY: dict[str, PaymentProvider] = {}


def register(provider: PaymentProvider) -> None:
    _REGISTRY[provider.name] = provider


def get(name: str) -> PaymentProvider:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ProviderError(
            f"unknown payment provider {name!r}; registered: {sorted(_REGISTRY)}") from None


def registered() -> list[str]:
    return sorted(_REGISTRY)


def _reset_for_tests() -> None:
    _REGISTRY.clear()
