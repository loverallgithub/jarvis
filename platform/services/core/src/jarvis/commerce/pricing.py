"""Tier pricing — DEC-001, mechanically.

Two rules, and the second is the one that bit Pimlico:

**1. Money is an integer in minor units, everywhere.** There is no float euro
in this module and no conversion helper that could be called twice. Pimlico
listed every one of its products at 100× because a float euro/cent confusion
went unnoticed through create, read and display; it was found only by reading
the value back from the provider. Integers-in-cents plus a read-back guard
makes the bug unrepresentable rather than merely unlikely.

**2. The ratio is a business decision; the anchor is researched.** Ratios live
in `pricing_policy` rows, so retuning the ladder is an UPDATE. The base price
comes per solution from Phase B willingness-to-pay evidence — observed prices
for adjacent solutions, captured as evidence with URLs. Never a regex over one
page, which is exactly how Pimlico arrived at €297.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from .. import db

Tier = Literal["roadmap", "instructions", "deployed"]
TIERS: tuple[Tier, ...] = ("roadmap", "instructions", "deployed")

# The ladder is an ordering, not just three prices — upgrades walk it upward.
TIER_RANK: dict[str, int] = {"roadmap": 0, "instructions": 1, "deployed": 2}


class PricingError(ValueError):
    """A price could not be computed safely. Never a silent fallback."""


@dataclass(frozen=True)
class TierPrice:
    tier: str
    price_minor: int
    ratio_used: Decimal

    @property
    def as_major(self) -> str:
        """For display and for logs ONLY. Never for arithmetic and never sent
        to a provider — formatting is where minor units get lost."""
        return f"{Decimal(self.price_minor) / 100:.2f}"


def _round_minor(value: Decimal) -> int:
    """Round to a whole minor unit, half-up.

    Bankers' rounding (Python's default) would make a €0.005 difference vanish
    in one direction half the time, which is defensible for statistics and
    indefensible for a price a customer sees.
    """
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


async def ladder(base_minor: int, *, round_to_minor: int = 100) -> list[TierPrice]:
    """Build the three-tier ladder from a researched anchor.

    `base_minor` is the Roadmap price in minor units — the 1× anchor.
    `round_to_minor` snaps each computed price to a tidy boundary (default: a
    whole euro). A price of €1,187.53 reads as arithmetic output rather than a
    considered offer.

    Ratios are read from the database at call time, so a retune takes effect
    without a deploy.
    """
    if not isinstance(base_minor, int) or isinstance(base_minor, bool):
        raise PricingError(
            f"base_minor must be an int in MINOR units, got {type(base_minor).__name__}. "
            f"A float here is the 100x bug waiting to happen.")
    if base_minor <= 0:
        raise PricingError(f"base_minor must be positive, got {base_minor}")

    rows = await db.fetch(
        "SELECT tier, ratio_min, ratio_max FROM pricing_policy")
    if len(rows) != 3:
        raise PricingError(
            f"pricing_policy has {len(rows)} rows, expected 3 — refusing to guess a ladder")

    policy = {r["tier"]: (Decimal(r["ratio_min"]), Decimal(r["ratio_max"])) for r in rows}

    out: list[TierPrice] = []
    for tier in TIERS:
        lo, hi = policy[tier]
        # Midpoint of the band. A band expresses a judgement range; picking the
        # midpoint is a defensible default that a human can override per offer.
        ratio = (lo + hi) / 2
        raw = Decimal(base_minor) * ratio
        snapped = _round_minor(raw / round_to_minor) * round_to_minor
        out.append(TierPrice(tier=tier, price_minor=max(snapped, 1), ratio_used=ratio))

    # The ladder must be strictly increasing or it is not a ladder — an
    # Instructions tier priced at or below Roadmap makes the upgrade path
    # nonsensical and the delta negative.
    for a, b in zip(out, out[1:]):
        if b.price_minor <= a.price_minor:
            raise PricingError(
                f"ladder is not strictly increasing: {a.tier}={a.price_minor} "
                f">= {b.tier}={b.price_minor}. Check pricing_policy ratios.")
    return out


def upgrade_delta_minor(from_price_minor: int, to_price_minor: int) -> int:
    """What a returning buyer pays to move up a tier.

    This is the highest-margin revenue in the system: the artifact already
    exists, so the delta is almost pure margin. It must never be negative and
    never zero — a free upgrade is a bug that looks like generosity.
    """
    delta = to_price_minor - from_price_minor
    if delta <= 0:
        raise PricingError(
            f"upgrade delta must be positive, got {delta} "
            f"({from_price_minor} -> {to_price_minor})")
    return delta


def is_upgrade(from_tier: str, to_tier: str) -> bool:
    try:
        return TIER_RANK[to_tier] > TIER_RANK[from_tier]
    except KeyError as e:
        raise PricingError(f"unknown tier {e.args[0]!r}") from None


def tiers_covered(tier: str) -> list[str]:
    """Each tier is a SUPERSET of the one below it.

    Instructions = Roadmap + build manual. Deployed = Instructions + the built
    thing. So an Instructions buyer is entitled to the Roadmap artifact too,
    and fulfilment must deliver accordingly.
    """
    if tier not in TIER_RANK:
        raise PricingError(f"unknown tier {tier!r}")
    return [t for t in TIERS if TIER_RANK[t] <= TIER_RANK[tier]]


def delta_tiers(from_tier: str, to_tier: str) -> list[str]:
    """The tiers an upgrade must deliver — ONLY the difference.

    G5: "on purchase, fulfil only the delta." Re-sending everything is not
    merely wasteful; it makes the buyer's inbox the place where our idempotency
    bugs become visible.
    """
    if not is_upgrade(from_tier, to_tier):
        raise PricingError(f"{from_tier} -> {to_tier} is not an upgrade")
    return [t for t in TIERS
            if TIER_RANK[from_tier] < TIER_RANK[t] <= TIER_RANK[to_tier]]
