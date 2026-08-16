"""G1–G2 — receive payment, verify it, grant the entitlement.

────────────────────────────────────────────────────────────────────────────
THE THREE CHECKS, AND WHY EACH ONE EXISTS
────────────────────────────────────────────────────────────────────────────
**1. Signature.** A failed signature cannot fulfil. Not "is logged and
continues" — cannot fulfil. Enforcement is on by default and an unconfigured
secret rejects.

**2. Amount, compared to the `offers` row.** The payload's amount is recorded
but never trusted. Pimlico treated any `amount > 0` as a paid order, so a
webhook claiming `amount: 1` would have minted a €297 product to whoever sent
it. Here the price comes from OUR row, keyed by the provider's product id, and
a mismatch rejects the order outright.

**3. Idempotency on `provider_ref`.** Providers retry. A replayed webhook must
find the existing order and change nothing — not create a second entitlement,
not re-deliver, not re-notify. Enforced by a UNIQUE constraint, not by a
check-then-insert race.

Every inbound webhook is written to `provider_events` BEFORE it is interpreted,
including rejected ones. When money is involved, "we never saw it" and "we
rejected it" must remain distinguishable months later.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

import structlog

from .. import db
from .providers.base import (ParsedOrder, PaymentProvider, UnparseableWebhook,
                             get as get_provider)

log = structlog.get_logger("commerce.orders")


@dataclass(frozen=True)
class ReceiveResult:
    accepted: bool
    reason: str
    order_id: Optional[int] = None
    entitlement_id: Optional[int] = None
    duplicate: bool = False

    @property
    def http_status(self) -> int:
        """Rejections return 200 to the PROVIDER on purpose.

        A 4xx makes most providers retry the same bad payload forever, which
        turns one malformed webhook into a permanent noise source. We record
        the rejection, we do not fulfil, and we tell the provider we are done
        with it. A 401 is reserved for a bad signature, where retrying is the
        correct provider behaviour once the secret is fixed.
        """
        if self.accepted:
            return 200
        return 401 if self.reason.startswith("signature") else 200


async def _record_event(provider: str, raw: bytes, sig_valid: bool, sig_reason: str,
                        accepted: bool, reject_reason: str = "",
                        provider_ref: str = "", event_type: str = "") -> int:
    return int(await db.fetchval(
        """
        INSERT INTO provider_events (provider, event_type, provider_ref, signature_valid,
                                     signature_reason, accepted, reject_reason, payload_raw)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id
        """,
        provider, event_type or None, provider_ref or None, sig_valid,
        sig_reason[:300], accepted, reject_reason[:500] or None,
        raw.decode("utf-8", "replace")[:50000]))


async def receive(provider_name: str, raw: bytes,
                  headers: dict[str, str]) -> ReceiveResult:
    """The whole of G1, in the order the checks must happen."""
    provider: PaymentProvider = get_provider(provider_name)

    # -- 1. signature, before anything is parsed or trusted ----------------
    sig = provider.verify_webhook(raw, headers)
    if sig.should_reject:
        await _record_event(provider_name, raw, sig.valid, sig.reason,
                            accepted=False, reject_reason=f"signature: {sig.reason}")
        log.warning("orders.signature_rejected", provider=provider_name, reason=sig.reason)
        return ReceiveResult(False, f"signature rejected: {sig.reason}")

    # -- 2. parse; unparseable is quarantined, never guessed at ------------
    try:
        payload = json.loads(raw or b"{}")
        parsed: ParsedOrder = provider.parse_order(payload)
    except (UnparseableWebhook, ValueError, TypeError) as e:
        await _record_event(provider_name, raw, sig.valid, sig.reason,
                            accepted=False, reject_reason=f"unparseable: {e}")
        log.warning("orders.unparseable", provider=provider_name, error=str(e)[:200])
        return ReceiveResult(False, f"unparseable webhook: {e}")

    # -- 3. the offer is OURS; the payload only says which one -------------
    offer = await db.fetchrow(
        """
        SELECT o.id, o.solution_id, o.tier, o.price_minor, o.currency, o.live
          FROM offers o
         WHERE o.provider = $1 AND o.external_ref = $2
        """,
        provider_name, parsed.external_ref)

    if offer is None:
        await _record_event(provider_name, raw, sig.valid, sig.reason, accepted=False,
                            reject_reason=f"unknown offer external_ref={parsed.external_ref}",
                            provider_ref=parsed.provider_ref, event_type=parsed.event_type)
        log.warning("orders.unknown_offer", external_ref=parsed.external_ref)
        return ReceiveResult(False, f"no offer for external_ref {parsed.external_ref!r}")

    # -- 4. amount and currency, from OUR row ------------------------------
    if parsed.amount_minor != int(offer["price_minor"]):
        reason = (f"amount mismatch: provider reported {parsed.amount_minor} minor, "
                  f"offer {offer['id']} is {offer['price_minor']} minor")
        await _record_event(provider_name, raw, sig.valid, sig.reason, accepted=False,
                            reject_reason=reason, provider_ref=parsed.provider_ref,
                            event_type=parsed.event_type)
        log.error("orders.amount_mismatch", offer_id=offer["id"],
                  reported=parsed.amount_minor, expected=int(offer["price_minor"]))
        return ReceiveResult(False, reason)

    if parsed.currency != offer["currency"]:
        reason = (f"currency mismatch: reported {parsed.currency}, "
                  f"offer is {offer['currency']}")
        await _record_event(provider_name, raw, sig.valid, sig.reason, accepted=False,
                            reject_reason=reason, provider_ref=parsed.provider_ref)
        return ReceiveResult(False, reason)

    if not offer["live"]:
        reason = f"offer {offer['id']} is not live"
        await _record_event(provider_name, raw, sig.valid, sig.reason, accepted=False,
                            reject_reason=reason, provider_ref=parsed.provider_ref)
        return ReceiveResult(False, reason)

    # -- 5. idempotent insert ---------------------------------------------
    # ON CONFLICT DO NOTHING against the UNIQUE (provider, provider_ref) index.
    # A check-then-insert would race two concurrent deliveries of the same
    # webhook into two orders and two entitlements.
    order_id = await db.fetchval(
        """
        INSERT INTO orders (offer_id, buyer_email, buyer_ref, amount_minor, currency,
                            provider, provider_ref, signature_valid, amount_matched,
                            status, raw_payload)
        VALUES ($1,$2,$3,$4,$5,$6,$7,TRUE,TRUE,'verified',$8::jsonb)
        ON CONFLICT (provider, provider_ref) DO NOTHING
        RETURNING id
        """,
        offer["id"], parsed.buyer_email, parsed.buyer_ref, parsed.amount_minor,
        parsed.currency, provider_name, parsed.provider_ref,
        json.dumps(parsed.raw, default=str))

    if order_id is None:
        existing = await db.fetchrow(
            "SELECT id FROM orders WHERE provider = $1 AND provider_ref = $2",
            provider_name, parsed.provider_ref)
        ent = await db.fetchval(
            "SELECT id FROM entitlements WHERE order_id = $1", existing["id"])
        await _record_event(provider_name, raw, sig.valid, sig.reason, accepted=True,
                            reject_reason="duplicate — no action taken",
                            provider_ref=parsed.provider_ref, event_type=parsed.event_type)
        log.info("orders.duplicate", provider_ref=parsed.provider_ref,
                 order_id=existing["id"])
        return ReceiveResult(True, "duplicate webhook — existing order unchanged",
                             order_id=int(existing["id"]),
                             entitlement_id=int(ent) if ent else None, duplicate=True)

    await _record_event(provider_name, raw, sig.valid, sig.reason, accepted=True,
                        provider_ref=parsed.provider_ref, event_type=parsed.event_type)

    # -- 6. G2: grant the entitlement -------------------------------------
    entitlement_id = await grant_entitlement(int(order_id))

    log.info("orders.accepted", order_id=int(order_id), offer_id=offer["id"],
             tier=offer["tier"], amount_minor=parsed.amount_minor)
    return ReceiveResult(True, "accepted", order_id=int(order_id),
                         entitlement_id=entitlement_id)


async def grant_entitlement(order_id: int) -> int:
    """G2. Tier-scoped, idempotent (UNIQUE on order_id).

    The tier comes from the OFFER, never from the payload — otherwise the buyer
    picks what they receive.
    """
    row = await db.fetchrow(
        """
        SELECT o.id AS order_id, o.buyer_ref, o.status,
               f.solution_id, f.tier
          FROM orders o JOIN offers f ON f.id = o.offer_id
         WHERE o.id = $1
        """, order_id)
    if row is None:
        raise LookupError(f"order {order_id} does not exist")
    if row["status"] not in ("verified", "fulfilled"):
        raise RuntimeError(
            f"refusing to grant an entitlement for order {order_id} in status "
            f"{row['status']!r} — only a verified order may be fulfilled")

    ent_id = await db.fetchval(
        """
        INSERT INTO entitlements (order_id, buyer_ref, solution_id, tier)
        VALUES ($1,$2,$3,$4)
        ON CONFLICT (order_id) DO NOTHING
        RETURNING id
        """,
        order_id, row["buyer_ref"], row["solution_id"], row["tier"])

    if ent_id is None:
        ent_id = await db.fetchval(
            "SELECT id FROM entitlements WHERE order_id = $1", order_id)
    return int(ent_id)


async def record_attribution(order_id: int, *, need_id: Optional[int] = None,
                             solution_id: Optional[int] = None,
                             source_type: Optional[str] = None,
                             channel: Optional[str] = None,
                             voice_id: Optional[int] = None) -> None:
    """G6 — which source type actually produced revenue.

    This is what eventually makes gate calibration data-driven instead of
    guessed. Until an order exists there is nothing to attribute, which is why
    Pimlico could never close this loop.
    """
    await db.execute(
        """
        INSERT INTO attributions (order_id, need_id, solution_id, source_type, channel, voice_id)
        VALUES ($1,$2,$3,$4,$5,$6)
        ON CONFLICT (order_id) DO UPDATE
          SET need_id = EXCLUDED.need_id, solution_id = EXCLUDED.solution_id,
              source_type = EXCLUDED.source_type, channel = EXCLUDED.channel,
              voice_id = EXCLUDED.voice_id
        """,
        order_id, need_id, solution_id, source_type, channel, voice_id)
