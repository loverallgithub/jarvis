"""Product cards — the KPIs and, above all, the BLOCKERS.

`offerable = structural AND factual` is the rule, but an operator reading
"factual_ok: false" learns nothing they can act on. The card's job is to answer
"why can't I sell this?" in the same words the verifier used, so these tests pin
the translation rather than the layout.
"""
from __future__ import annotations

import json

from jarvis.console.dashboard import _blockers, _product_card, _products

PRICING = {
    "roadmap": {"ratio_min": 1, "ratio_max": 1},
    "instructions": {"ratio_min": 3, "ratio_max": 4},
    "deployed": {"ratio_min": 10, "ratio_max": 15},
}


def _art(**kw):
    base = dict(
        id=8, need_id=13, tier="deployed", words=4815, sections=8, bytes=1,
        structural_ok=False, factual_ok=False, offerable=False,
        sha256="abc123def456", created_at=None,
        storage_uri="file:///app/data/artifacts/need-13-deployed-x.md",
        verify_detail=None, need_title="payabl / automat / account",
        audience="SMBs", claims_cited=14, claims_supported=10,
        claims_unsupported=4, tests_total=32, tests_passed=0, sources_cited=5,
        price_minor=None, currency=None, offer_live=None, checkout_url=None)
    base.update(kw)
    return base


# ── blockers ───────────────────────────────────────────────────────────────
def test_unsupported_claims_are_reported_with_a_count():
    assert "4 unsupported claims" in _blockers(_art())


def test_one_unsupported_claim_is_not_pluralised():
    assert "1 unsupported claim" in _blockers(_art(claims_unsupported=1))


def test_thin_and_missing_sections_are_named_not_just_counted():
    """'structural failed' sends you to read the file; the section name does not."""
    detail = json.dumps({"thin_sections": ["estimate (4 words)"],
                         "missing_sections": ["audience"]})
    out = " | ".join(_blockers(_art(verify_detail=detail)))
    assert "estimate (4 words)" in out
    assert "audience" in out


def test_verify_detail_as_a_dict_works_as_well_as_a_json_string():
    """asyncpg returns jsonb as str on some paths and dict on others; the card
    must not depend on which."""
    d = {"placeholders": ["bracketed placeholder"]}
    assert _blockers(_art(verify_detail=d)) == _blockers(
        _art(verify_detail=json.dumps(d)))


def test_unparseable_verify_detail_does_not_crash_the_page():
    out = _blockers(_art(verify_detail="{not json"))
    assert "4 unsupported claims" in out


def test_a_structural_failure_with_no_detail_still_says_something():
    """Silence would read as 'no problem' next to a withheld badge."""
    out = _blockers(_art(claims_unsupported=0, verify_detail=None))
    assert out and "structural" in out[0]


def test_a_clean_product_has_no_blockers():
    assert _blockers(_art(claims_unsupported=0, structural_ok=True,
                          factual_ok=True, offerable=True)) == []


# ── card ───────────────────────────────────────────────────────────────────
def test_the_card_shows_offerable_only_when_it_is():
    assert "withheld" in _product_card(_art(), PRICING)
    assert "OFFERABLE" in _product_card(
        _art(offerable=True, structural_ok=True, factual_ok=True,
             claims_unsupported=0), PRICING)


def test_price_ratio_renders_as_a_range_and_drops_trailing_zeros():
    html = _product_card(_art(tier="instructions"), PRICING)
    assert "3–4×" in html
    assert "3.00" not in html


def test_a_fixed_ratio_is_not_rendered_as_a_range():
    assert "1×" in _product_card(_art(tier="roadmap"), PRICING)


def test_the_card_links_to_the_product_and_its_raw_markdown():
    html = _product_card(_art(), PRICING)
    assert 'href="/artifact/8"' in html
    assert 'href="/artifact/8?raw=1"' in html


def test_a_checkout_url_appears_only_when_an_offer_exists():
    assert "checkout" not in _product_card(_art(), PRICING)
    assert "checkout" in _product_card(
        _art(checkout_url="https://pay.example/x"), PRICING)


def test_an_unpriced_product_says_so_rather_than_showing_zero():
    """'0.00' would read as free, which is a different and much worse claim."""
    html = _product_card(_art(price_minor=None), PRICING)
    assert "not priced" in html
    assert "0.00" not in html


def test_the_card_escapes_hostile_text():
    html = _product_card(_art(need_title="<script>alert(1)</script>"), PRICING)
    assert "<script>" not in html


# ── family grouping ────────────────────────────────────────────────────────
def test_tiers_are_ordered_as_the_ladder_not_by_id():
    """Asserted on the tier LABELS, not on substring position in the page.

    A first version searched the whole HTML for the words, and every card's
    storage_uri contains the tier name — so it read a path in the roadmap card
    as evidence about the deployed card and failed against correct output.
    """
    import re
    arts = [_art(id=8, tier="deployed"), _art(id=6, tier="roadmap"),
            _art(id=7, tier="instructions")]
    html = _products(arts, PRICING, [{"id": 13, "audience": "SMBs"}])
    labels = re.findall(r'<span class="ptier">([a-z]+)</span>', html)
    assert labels == ["roadmap", "instructions", "deployed"]


def test_the_family_header_counts_sellable_tiers():
    arts = [_art(id=6, tier="roadmap", offerable=True),
            _art(id=7, tier="instructions"), _art(id=8, tier="deployed")]
    assert "1/3 sellable" in _products(arts, PRICING, [])


def test_no_products_says_so_instead_of_rendering_an_empty_shell():
    assert "no products" in _products([], PRICING, [])
