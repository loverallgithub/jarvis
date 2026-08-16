"""A currency figure is not a price.

FOUND 2026-08-09 by the claim-level verifier — the first thing that ever checked
these individually:

    "$33.4 Million Recovered"    -> stored as "USD 33.00"   (highradius.com)
    "$5M+ in fraud identified"   -> stored as "USD 5.00"    (ramp.com)

Two independent faults. The MAGNITUDE SUFFIX was dropped, turning 33.4 million
into 33; and nothing checked whether the surrounding words were about price at
all.

Both claims fed `willingness_to_pay`, which anchors the tier ladder. The live
test ladder was therefore priced partly off a fraud statistic and a recovery
total. Pimlico priced €297 products from a regex over one page; this is the same
failure wearing a citation.

REJECTING IS THE RIGHT DEFAULT. A missed real price costs one data point. An
invented one silently sets what the product sells for.
"""
from __future__ import annotations

import pytest

from jarvis.research.dossier import is_price_context


# ── the two observed failures ──────────────────────────────────────────────
def test_millions_recovered_is_not_a_price():
    assert is_price_context("Explore 15 customers $33.4 Million Recovered",
                            " Million Recovered") is False


def test_fraud_identified_is_not_a_price():
    assert is_price_context("Ramp identified $5M+ in fraud across customers",
                            "M+ in fraud") is False


# ── magnitude suffixes ─────────────────────────────────────────────────────
@pytest.mark.parametrize("tail", [" million", " Million", "M", "m", " bn",
                                  "B", " billion", "k", " thousand"])
def test_any_magnitude_suffix_rejects_the_figure(tail):
    assert is_price_context("the company processed $12 last year", tail) is False


def test_a_plain_number_is_not_rejected_by_the_magnitude_rule():
    assert is_price_context("Plans start at $49 per user", " per user") is True


# ── aggregate vocabulary ───────────────────────────────────────────────────
@pytest.mark.parametrize("ctx", [
    "we recovered $33 for clients",
    "the round raised $20 from investors",
    "annual revenue of $80 reported",
    "total transactions worth $45 processed",
    "customers saved $60 on average in losses",
])
def test_aggregate_language_rejects_the_figure(ctx):
    assert is_price_context(ctx, "") is False


# ── real prices survive ────────────────────────────────────────────────────
@pytest.mark.parametrize("ctx", [
    "Ramp Plus: $15 per user/mo",
    "Pricing starts at $99/mo for the growth plan",
    "The tool costs $49 a month",
    "Enterprise tier billed at $80 per seat",
    "Subscription price $29",
])
def test_a_genuine_price_is_accepted(ctx):
    assert is_price_context(ctx, "") is True


def test_price_vocabulary_rescues_a_figure_the_anti_list_would_reject():
    """"$15 per user/mo, saving $200 in losses" is still a price sentence —
    the explicit price marker wins over an incidental aggregate word."""
    assert is_price_context("Ramp Plus: $15 per user/mo, saving on losses",
                            " per user/mo") is True


def test_a_bare_table_cell_price_is_kept():
    """Many real prices sit in a table with no surrounding prose. The anti-list
    is what rejects, not the absence of price words."""
    assert is_price_context("| Starter | $29 | 5 seats |", " | 5 seats") is True


def test_empty_input_is_safe():
    assert is_price_context("", "") is True
    assert is_price_context(None, None) is True     # type: ignore[arg-type]
