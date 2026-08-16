"""The fact-checker must be shown the part of the page the claim is about.

Regression cover for three withheld artifacts. On 2026-08-08 `factual()` selected
its excerpt with `left(e.body, 2500)`. Claims 30 and 31 cite a page whose vendor
comparison names **Tipalti at character 3228** — 728 characters past the window.
The verifier replied "the source excerpt does not contain any pricing
information … or mentions of Stampli, Yooz, or Tipalti", which was true of the
2500 characters it was given and false of the evidence.

That is the dangerous shape: the rejection reason is a TRUE statement about the
input, so it reads as a content problem and sends you upstream to fix evidence
capture that was never broken. These tests pin the selection so the window can
never again be the reason a claim fails.
"""
from __future__ import annotations

from jarvis.forge.verify import EXCERPT_WIDTH, relevant_excerpt

NAV = ("Products Partners Solutions Resources Customers Pricing Sign in "
       "See a demo Back Blog In this article What is accounts payable "
       "automation software At a glance Comparing the best AP software ")


def _page(filler_before: int, needle: str, tail: str = "") -> str:
    """A page with `needle` pushed past `filler_before` characters."""
    filler = (NAV * ((filler_before // len(NAV)) + 1))[:filler_before]
    return filler + needle + tail


def test_the_observed_failure_tipalti_past_2500_is_now_in_the_excerpt():
    """The exact bug, at the exact offset that caused it."""
    body = _page(3228, "Tipalti Elevate pricing is NA - Custom quote only.",
                 " " + NAV * 20)
    assert "tipalti" not in body[:EXCERPT_WIDTH].lower()      # the old behaviour
    out = relevant_excerpt(body, "Tipalti Elevate shows NA - Custom pricing")
    assert "Tipalti" in out
    assert "NA - Custom quote only" in out


def test_a_body_shorter_than_the_window_is_returned_whole():
    body = "Short page about Stampli pricing."
    assert relevant_excerpt(body, "Stampli pricing") == body


def test_the_page_head_is_always_kept_so_the_page_can_be_identified():
    """Without the title the model cannot tell WHAT it is reading."""
    body = "Best AP Automation Software 2026 | Ramp. " + _page(6000, "Yooz costs $199/mo.")
    out = relevant_excerpt(body, "Yooz costs $199 per month")
    assert "Best AP Automation Software 2026" in out
    assert "Yooz costs $199/mo." in out
    assert "[…]" in out


def test_a_hit_inside_the_head_returns_a_plain_leading_window_not_a_spliced_one():
    body = "Tipalti is named immediately. " + NAV * 300
    out = relevant_excerpt(body, "Tipalti is named")
    assert out == body[:EXCERPT_WIDTH]
    assert "[…]" not in out


def test_a_claim_with_no_usable_terms_falls_back_to_the_head():
    body = NAV * 300
    out = relevant_excerpt(body, "the and of to in")     # all stopwords
    assert out == body[:EXCERPT_WIDTH]


def test_no_keyword_matches_anywhere_falls_back_to_the_head():
    body = NAV * 300
    out = relevant_excerpt(body, "quantum cryptography satellites")
    assert out == body[:EXCERPT_WIDTH]


def test_the_window_with_MORE_of_the_claims_terms_wins():
    """Coverage beats density — a window naming three vendors beats one
    repeating a single vendor, because a claim usually spans several terms."""
    dense = ("Stampli " * 40)
    broad = "Stampli and Yooz and Tipalti all show custom pricing. "
    body = NAV * 40 + dense + NAV * 120 + broad + NAV * 120
    out = relevant_excerpt(body, "Stampli Yooz Tipalti custom pricing")
    assert "Yooz" in out and "Tipalti" in out


def test_an_empty_body_never_crashes():
    assert relevant_excerpt("", "any claim") == ""
    assert relevant_excerpt(None, "any claim") == ""          # type: ignore[arg-type]


def test_selection_is_deterministic():
    body = _page(4000, "Tipalti Elevate custom pricing.", NAV * 50)
    claim = "Tipalti Elevate custom pricing"
    assert relevant_excerpt(body, claim) == relevant_excerpt(body, claim)


def test_the_excerpt_stays_bounded():
    """It is sent to an LLM on every claim of every artifact — it must not grow
    with page size, or a 400KB page becomes a 400KB prompt."""
    body = _page(6000, "Tipalti.", NAV * 500)
    out = relevant_excerpt(body, "Tipalti")
    assert len(out) <= EXCERPT_WIDTH + 400 + 16
