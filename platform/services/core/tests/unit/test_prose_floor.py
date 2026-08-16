"""Excerpt selection must not hand the fact-checker a navigation menu.

The story, because the numbers matter:

  v1  score = coverage, then DENSITY. Menus are the densest keyword text on any
      page, so a sidebar won outright.
  v2  prose added as a secondary sort key. Not enough — a link list naming every
      ERP covers every term of a claim about ERP integration, so it won on
      COVERAGE and the tiebreak never ran. Claims 33 and 34 still selected
      windows scoring 0.21 and 0.62.
  v3  prose as a FLOOR at 0.35. Still not enough: 0.43 and 0.62 chrome passed
      the floor and then won on coverage anyway.
  v4  floor at 0.75, above the measured chrome. Claims 28/32/33 cleared — and
      claim 31 REGRESSED, because a pricing table is fragments, not sentences,
      so the floor pushed selection off the very table the claim was about.
  v5  send BOTH the best-coverage and best-prose window. The claim type decides
      which shape of text supports it, and that cannot be known without asking a
      model — which is exactly what must not choose the verifier's own input.
  v6  ANCHOR ON QUOTED PHRASES. Claim 31 still failed: it quotes 'NA - Custom
      quote' from its source, that text sits at offset 3079, and every
      top-coverage window sat at 6875-8750. When a claim quotes the source, the
      quote is evidence of WHERE to look and beats any keyword proxy. Fixing
      this also exposed a tokeniser bug: `_WORD` allowed a trailing apostrophe,
      so the quoted phrase produced the term `quote'`, which can never match
      "quote" in any body — the one case where the words matter most was the one
      case the tokeniser mangled.

Measured scores on real pages: raw nav 0.08 · article-title list 0.43 · nav with
prepositions 0.62 · article prose 0.92-1.00. The evidence bodies each contain
35-70 windows at >= 0.85, so a high floor is not scarce.
"""
from __future__ import annotations

from jarvis.forge.verify import (EXCERPT_WIDTH, PROSE_FLOOR, prose_score,
                                 relevant_excerpt)

NAV = ("Products Partners Solutions Resources Customers Pricing Sign in See a demo "
       "Home Product Single Platform Office of the CFO Order To Cash Accounts Payable "
       "B2B Payments Consolidation Reporting Close Reconciliation Treasury Risk "
       "Integrations API ERP NetSuite SAP Oracle MS Dynamics Blog Careers Contact ")

PROSE = ("Most finance teams still key invoices by hand, and the cost of that is not "
         "the typing but the exceptions. When a purchase order does not match the "
         "invoice, someone has to chase it, and that person is usually the one who "
         "also runs the month-end close. Integration with an existing ERP is "
         "therefore the first question to ask, because a tool that cannot write back "
         "to the ledger simply moves the work somewhere else. ")


# ── the discriminator ──────────────────────────────────────────────────────
def test_navigation_scores_far_below_prose():
    assert prose_score(NAV * 3) < 0.2
    assert prose_score(PROSE * 3) > 0.8


def test_the_floor_sits_between_them():
    assert prose_score(NAV * 3) < PROSE_FLOOR < prose_score(PROSE * 3)


def test_a_very_short_window_is_not_called_prose():
    """Too little text to judge — must not score high by accident."""
    assert prose_score("Pricing ERP API") == 0.0


def test_empty_input_is_safe():
    assert prose_score("") == 0.0


# ── selection ──────────────────────────────────────────────────────────────
def test_prose_is_chosen_over_a_menu_that_covers_MORE_of_the_claim():
    """The exact production failure: the menu covers every term, the article
    covers fewer, and the article must still win."""
    menu = ("Integrations API ERP NetSuite SAP Oracle Pricing Plans Compare " * 30)
    body = menu + (PROSE * 10) + menu
    out = relevant_excerpt(body, "ERP integration pricing")
    # Assert the selected WINDOW reads as prose — not that it contains zero menu
    # characters. Two earlier versions of this test asserted the wrong thing and
    # failed against correct output: the first 400 chars are the page head
    # (included deliberately, so the model knows what it is reading, and here the
    # head IS menu), and a 2500-char window straddling the boundary legitimately
    # carries some of both. What matters is which side dominates.
    window = out.split("[…]", 1)[-1]
    assert "purchase order does not match" in window
    assert prose_score(window) >= PROSE_FLOOR


def test_a_page_that_is_ALL_chrome_still_returns_its_best_window():
    """Falling back matters: the verifier must be able to say 'this source does
    not support the claim' rather than fail to answer at all."""
    body = NAV * 40
    out = relevant_excerpt(body, "ERP integration")
    assert out and len(out) <= EXCERPT_WIDTH + 400 + 16


def test_a_body_shorter_than_the_window_is_returned_whole():
    body = "Short page about ERP integration pricing."
    assert relevant_excerpt(body, "ERP integration") == body


def test_the_selected_window_is_still_relevant_not_merely_prose():
    """The floor must not cost relevance — coverage is still the first key
    among eligible windows."""
    filler = ("The weather in October was unremarkable and the office was quiet, "
              "which suited everyone who had deadlines to meet. ") * 30
    target = ("Tipalti Elevate lists no public price and quotes on request, which "
              "makes budgeting difficult for a small finance team. ") * 3
    body = filler + target + filler
    out = relevant_excerpt(body, "Tipalti Elevate custom pricing quote")
    assert "Tipalti Elevate" in out


def test_selection_stays_deterministic():
    body = (NAV * 10) + (PROSE * 8) + (NAV * 10)
    claim = "ERP integration invoice"
    assert relevant_excerpt(body, claim) == relevant_excerpt(body, claim)


def test_the_excerpt_stays_bounded():
    body = (PROSE * 200)
    out = relevant_excerpt(body, "ERP integration invoice")
    assert len(out) <= EXCERPT_WIDTH + 400 + 16


# ── the mirror failure: tables and lists ARE evidence ──────────────────────
# The prose floor cleared claims 28/32/33 and simultaneously REGRESSED claim 31
# ("does not contain any pricing information, comparison tables") and left 34
# failing for the same reason. A pricing table and an integrations list are
# fragments, not sentences, so they score as chrome — but for a claim about
# transparent pricing, the table IS the evidence. Hence: send BOTH windows.

TABLE = ("Vendor Plan Price Stampli NA - Custom quote Yooz NA - Custom quote "
         "Tipalti Elevate NA - Custom pricing based on document volume "
         "Ramp Free 0/mo Ramp Plus 15 per user/mo Enterprise custom ")


def test_a_pricing_table_is_not_lost_to_the_prose_floor():
    """The exact regression: claim 31 passed, then the floor pushed selection
    off the pricing table and it failed."""
    body = (PROSE * 12) + (TABLE * 8) + (PROSE * 12)
    out = relevant_excerpt(body, "Stampli Yooz Tipalti Elevate custom pricing quote")
    assert "NA - Custom quote" in out
    assert "Tipalti Elevate" in out


def test_both_a_table_and_prose_can_reach_the_fact_checker():
    """When the best-coverage and best-prose windows are different parts of the
    page, the verifier gets both rather than whichever heuristic won."""
    body = (TABLE * 10) + (PROSE * 20)
    out = relevant_excerpt(body, "Tipalti Elevate custom pricing")
    assert "Tipalti Elevate" in out
    assert out.count("[…]") >= 1


def test_overlapping_windows_are_not_sent_twice():
    """Two near-identical windows would spend tokens repeating themselves."""
    body = (PROSE * 30)
    out = relevant_excerpt(body, "ERP integration invoice")
    assert out.count("[…]") <= 1


def test_the_dual_excerpt_is_still_bounded():
    body = (TABLE * 20) + (PROSE * 40)
    out = relevant_excerpt(body, "Tipalti pricing ERP integration invoice")
    assert len(out) <= 2 * EXCERPT_WIDTH + 400 + 32


# ── quoted phrases are locators, not keywords ──────────────────────────────
def test_a_phrase_the_claim_QUOTES_is_found_even_when_coverage_points_elsewhere():
    """Claim 31, exactly. Its quoted phrase sat at offset 3079 while every
    top-coverage window sat at 6875-8750, so the fact-checker got the vendor
    names without their prices and reported no pricing information."""
    table = "Stampli NA - Custom quote Yooz NA - Custom quote Tipalti Elevate "
    decoy = ("Stampli and Yooz and Tipalti Elevate are compared on pricing and "
             "vendors and information throughout this transparent section. ") * 20
    body = (PROSE * 4) + table + (PROSE * 4) + decoy + (PROSE * 10)
    claim = ("Lack of transparent pricing information - multiple vendors "
             "(Stampli, Yooz, Tipalti Elevate) show 'NA - Custom quote'")
    assert "NA - Custom quote" in relevant_excerpt(body, claim)


def test_curly_quotes_are_honoured_too():
    body = (PROSE * 6) + "the vendor shows NA - Custom quote here " + (PROSE * 6)
    assert "NA - Custom quote" in relevant_excerpt(body, "vendors show ‘NA - Custom quote’")


def test_a_quoted_phrase_absent_from_the_body_is_ignored_not_fatal():
    body = PROSE * 20
    out = relevant_excerpt(body, "the page says 'this phrase is nowhere on it'")
    assert out and len(out) <= 2 * EXCERPT_WIDTH + 400 + 32


def test_a_very_short_quoted_fragment_is_not_used_as_an_anchor():
    """Anchoring on 'AP' or 'ROI' would match the first incidental occurrence
    and be worse than the heuristic it overrides."""
    body = ("AP " * 400) + (PROSE * 10)
    out = relevant_excerpt(body, "the page mentions 'AP' repeatedly")
    assert out


def test_a_trailing_apostrophe_no_longer_breaks_a_term():
    """`quote'` could never match "quote" in any body — the one case where the
    words matter most was the one the tokeniser mangled."""
    from jarvis.forge.verify import _terms
    terms = _terms("vendors show 'NA - Custom quote' on the page")
    assert "quote" in terms
    assert not any(t.endswith("'") for t in terms)
