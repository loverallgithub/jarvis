"""Creating the three-tier offer ladder for a solution.

The ladder is created as a unit or not at all. A solution with a live Deployed
offer and no Roadmap offer is not a ladder — it is the expensive tier with its
on-ramp missing, and the upgrade path (the highest-margin revenue in the
system) cannot exist without the rungs below.

Offers are created `live = FALSE`. Going live is a separate, deliberate act
(`publish`), because creating a purchasable thing and deciding to sell it are
different decisions and the second one is outward-facing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import structlog

from .. import db
from .pricing import TIERS, PricingError, is_upgrade, ladder, upgrade_delta_minor
from .providers.base import ProviderError, get as get_provider

log = structlog.get_logger("commerce.offers")


@dataclass(frozen=True)
class CreatedOffer:
    offer_id: int
    tier: str
    price_minor: int
    external_ref: str
    checkout_url: str


async def create_ladder(solution_id: int, *, base_minor: int, provider: str = "ghl_payments",
                        currency: str = "EUR", store_id: Optional[str] = None,
                        name_prefix: str = "") -> list[CreatedOffer]:
    """Create all three offers with the provider and record them locally."""
    sol = await db.fetchrow("SELECT id, title FROM solutions WHERE id = $1", solution_id)
    if sol is None:
        raise LookupError(f"solution {solution_id} does not exist")

    prices = await ladder(base_minor)
    p = get_provider(provider)
    created: list[CreatedOffer] = []

    for tp in prices:
        title = f"{name_prefix or sol['title']} — {tp.tier.title()}"
        po = await p.create_offer(
            name=title,
            description=f"{tp.tier} tier for solution {solution_id}",
            tier=tp.tier, price_minor=tp.price_minor,
            currency=currency, store_id=store_id)

        offer_id = await db.fetchval(
            """
            INSERT INTO offers (solution_id, tier, currency, price_minor, external_ref,
                                provider, store_id, checkout_url, live)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,FALSE)
            ON CONFLICT (solution_id, tier) DO UPDATE
              SET price_minor = EXCLUDED.price_minor,
                  external_ref = EXCLUDED.external_ref,
                  checkout_url = EXCLUDED.checkout_url,
                  store_id = EXCLUDED.store_id
            RETURNING id
            """,
            solution_id, tp.tier, currency, tp.price_minor, po.external_ref,
            provider, po.store_id, po.checkout_url)

        created.append(CreatedOffer(int(offer_id), tp.tier, tp.price_minor,
                                    po.external_ref, po.checkout_url))
        log.info("offers.created", solution_id=solution_id, tier=tp.tier,
                 price_minor=tp.price_minor, offer_id=int(offer_id))

    return created


async def publish(solution_id: int) -> int:
    """Make the ladder purchasable — all three rungs, or none.

    Refusing a partial ladder here is what stops a Deployed-only listing, where
    the cheapest way in does not exist and every visitor who was not ready to
    spend 10-15x simply leaves.

    Three things must hold, and all three are about the buyer rather than us:
    every tier exists locally, every tier exists at the provider, and every
    tier has an artifact that PASSED verification. The third was missing until
    2026-08-09 — `forge` could withhold a deliverable while `publish` sold it.
    """
    rows = await db.fetch(
        "SELECT tier, price_minor, external_ref FROM offers WHERE solution_id = $1",
        solution_id)
    have = {r["tier"] for r in rows}
    missing = set(TIERS) - have
    if missing:
        raise ProviderError(
            f"refusing to publish solution {solution_id}: missing tiers {sorted(missing)}. "
            f"A ladder is published whole or not at all.")

    for r in rows:
        if not r["external_ref"]:
            raise ProviderError(
                f"tier {r['tier']} has no external_ref — it does not exist at the "
                f"provider, so it cannot be bought")

    # 🔴 EVERY RUNG MUST HAVE A VERIFIED DELIVERABLE BEHIND IT.
    #
    # `forge reverify` withholds an artifact that fails its structural or
    # factual pass, and until 2026-08-09 that verdict stopped at the forge:
    # `publish` read `offers` and never looked at `artifacts`. The gate was on
    # the side that does not take money and absent from the side that does, so
    # a withheld artifact could be sold anyway by publishing its offer.
    #
    # Keyed on solution_id, newest-first — the SAME lookup as
    # `fulfilment._artifact_for`, deliberately. Gating on a different row than
    # the one that ships is not a gate: an artifact can carry both a need_id
    # and a solution_id, and checking need_id would happily approve a verified
    # sibling while fulfilment delivered the newest unverified row instead.
    #
    # Refused rather than filtered, and refused for the WHOLE ladder, for the
    # same reason as the missing-tier check above: a ladder with the middle
    # rung silently skipped is not a smaller ladder, it is a broken one.
    problems: list[str] = []
    for tier in TIERS:
        a = await db.fetchrow(
            "SELECT id, offerable, structural_ok, factual_ok FROM artifacts "
            "WHERE solution_id = $1 AND tier = $2 ORDER BY id DESC LIMIT 1",
            solution_id, tier)
        if a is None:
            problems.append(f"{tier}: no artifact has been built")
        elif not a["offerable"]:
            why = []
            if a["structural_ok"] is False:
                why.append("structural")
            if a["factual_ok"] is False:
                why.append("factual")
            # NULL on both means it was never verified, which is not the same
            # as failing and must not be reported as though it were.
            detail = (f"failed its {' and '.join(why)} check" if why
                      else "has not been verified")
            problems.append(f"artifact {a['id']} ({tier}) {detail}")

    if problems:
        need_id = await db.fetchval(
            "SELECT need_id FROM solutions WHERE id = $1", solution_id)
        raise ProviderError(
            f"refusing to publish solution {solution_id}: "
            + "; ".join(problems)
            + (f". Run `jpd forge reverify {need_id}` and fix what it reports"
               if need_id else "")
            + " — an offer that can be bought must have a deliverable that passed.")

    n = await db.fetchval(
        "WITH u AS (UPDATE offers SET live = TRUE WHERE solution_id = $1 RETURNING 1) "
        "SELECT count(*) FROM u", solution_id)
    log.info("offers.published", solution_id=solution_id, offers=int(n))
    return int(n)


async def upgrade_quote(entitlement_id: int, to_tier: str) -> dict:
    """What a returning buyer pays to move up. G5.

    Prices come from the live `offers` rows, never recomputed from ratios — the
    buyer paid what the offer said, and the delta must be against that same
    number even if the ladder has since been retuned.
    """
    ent = await db.fetchrow(
        "SELECT e.id, e.tier, e.solution_id, e.revoked_at, o.price_minor AS paid_minor "
        "FROM entitlements e JOIN orders r ON r.id = e.order_id "
        "JOIN offers o ON o.id = r.offer_id WHERE e.id = $1", entitlement_id)
    if ent is None:
        raise LookupError(f"entitlement {entitlement_id} does not exist")
    if ent["revoked_at"] is not None:
        raise ProviderError("entitlement is revoked — no upgrade available")

    # Check the DIRECTION before the arithmetic. Letting a downgrade fall
    # through to `upgrade_delta_minor` raises "delta must be positive", which
    # describes the symptom and hides the cause — the caller asked to move
    # DOWN the ladder, which is a different problem with a different fix.
    if not is_upgrade(ent["tier"], to_tier):
        raise PricingError(
            f"{ent['tier']} -> {to_tier} is not an upgrade; "
            f"the ladder only moves upward")

    target = await db.fetchrow(
        "SELECT id, price_minor, currency, checkout_url, live FROM offers "
        "WHERE solution_id = $1 AND tier = $2", ent["solution_id"], to_tier)
    if target is None:
        raise LookupError(f"no {to_tier} offer for solution {ent['solution_id']}")
    if not target["live"]:
        raise ProviderError(f"the {to_tier} offer is not live")

    delta = upgrade_delta_minor(int(ent["paid_minor"]), int(target["price_minor"]))
    return {"entitlement_id": entitlement_id, "from_tier": ent["tier"], "to_tier": to_tier,
            "paid_minor": int(ent["paid_minor"]), "target_minor": int(target["price_minor"]),
            "delta_minor": delta, "currency": target["currency"],
            "target_offer_id": int(target["id"]), "checkout_url": target["checkout_url"]}


async def record_upgrade(from_entitlement_id: int, to_tier: str, delta_minor: int,
                         order_id: Optional[int] = None) -> int:
    return int(await db.fetchval(
        "INSERT INTO upgrades (from_entitlement_id, to_tier, price_delta_minor, order_id) "
        "VALUES ($1,$2,$3,$4) RETURNING id",
        from_entitlement_id, to_tier, delta_minor, order_id))
