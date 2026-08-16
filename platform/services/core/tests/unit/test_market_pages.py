"""F5 — the sales page: escaping, the Buy button, and what makes it publishable.

Two properties carry real weight here.

ESCAPING. The page body is MODEL OUTPUT rendered onto a public page. A general
markdown renderer would happily pass raw HTML straight through, so the renderer
escapes everything first and re-introduces exactly four constructs.

THE BUY BUTTON. A page whose Buy button goes nowhere is worse than no page: it
burns the launch audience once and they do not come back. Pimlico shipped three
delivery tokens pointing at files that did not exist — this is the same failure
one step earlier in the funnel.
"""
from __future__ import annotations

import pytest

from jarvis.market import pages
from jarvis.market.copy import COVERAGE_FLOOR


def _data(*, live=True, url="https://pay.test/p/1", tiers=("roadmap",),
          body="Teams lose 12 hours a week [claim 4]."):
    return {
        "need": {"id": 13, "title": "AP automation"},
        "pos": {"pain_phrase": "chasing invoices", "promise": "cut it to an hour",
                "proof": "Measured across 40 firms [claim 4]."},
        "blocks": {t: {"headline": {"body": body, "citation_pct": 100.0}}
                   for t in tiers},
        "offers": {t: {"tier": t, "price_minor": 9900, "currency": "EUR",
                       "checkout_url": url, "live": live} for t in tiers},
    }


# ── escaping ───────────────────────────────────────────────────────────────
def test_model_output_cannot_inject_html():
    """Assert on the INJECTED payload, not on `<script>` generally — the page
    ships its own tab-switcher script, so a blanket check fails against
    correct output."""
    html = pages.render(_data(body="<script>alert(1)</script> and 12 hours [claim 4]"))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_a_hostile_title_is_escaped():
    d = _data()
    d["need"]["title"] = '"><img src=x onerror=alert(1)>'
    html = pages.render(d)
    assert "onerror=alert(1)>" not in html


def test_the_four_supported_constructs_still_render():
    html = pages.render(_data(body="**bold** and *em* and `code` [claim 7]"))
    assert "<strong>bold</strong>" in html
    assert "<em>em</em>" in html
    assert "<code>code</code>" in html


def test_citations_become_visible_superscripts():
    """A citation the reader cannot see is a citation that does no work."""
    html = pages.render(_data(body="Teams lose 12 hours [claim 4]."))
    assert '<sup class="cite">4</sup>' in html
    assert "[claim 4]" not in html


def test_bullets_render_as_a_list():
    html = pages.render(_data(body="- first point [claim 1]\n- second point [claim 2]"))
    assert html.count("<li>") == 2 and "<ul>" in html


# ── the buy button ─────────────────────────────────────────────────────────
def test_a_live_offer_renders_a_buy_button_with_the_price():
    html = pages.render(_data())
    assert 'class="buy"' in html
    assert "99.00 EUR" in html
    assert "https://pay.test/p/1" in html


def test_an_offer_that_is_not_live_renders_NO_button():
    html = pages.render(_data(live=False))
    assert 'class="buy"' not in html
    assert "Not yet available" in html


def test_a_missing_checkout_url_renders_NO_button():
    """The exact Pimlico failure, one step earlier: a control that points
    nowhere is worse than an absent control."""
    html = pages.render(_data(url=""))
    assert 'class="buy"' not in html


# ── structure ──────────────────────────────────────────────────────────────
def test_three_tiers_give_three_tabs_and_one_visible_panel():
    import re
    html = pages.render(_data(tiers=("roadmap", "instructions", "deployed")))
    assert html.count('class="tab"') == 3
    # Count on the BUTTONS. `aria-selected=true` also appears in the CSS
    # selector `.tab[aria-selected=true]`, so a whole-document count is 2 for a
    # perfectly correct page.
    buttons = re.findall(r'<button class="tab"[^>]*>', html)
    assert sum(1 for b in buttons if "aria-selected=true" in b) == 1
    assert sum(1 for b in buttons if "aria-selected=false" in b) == 2
    assert html.count("<div class=\"panel\"") == 3


def test_tiers_render_in_ladder_order():
    html = pages.render(_data(tiers=("deployed", "roadmap", "instructions")))
    order = [t for t in ("Roadmap", "Instructions", "Deployed") if t in html]
    assert order == ["Roadmap", "Instructions", "Deployed"]
    assert html.index("Roadmap") < html.index("Instructions") < html.index("Deployed")


def test_the_page_declares_a_charset_and_a_viewport():
    html = pages.render(_data())
    assert 'charset="utf-8"' in html or "charset=utf-8" in html
    assert "viewport" in html


def test_the_coverage_floor_is_a_real_bar_not_a_rubber_stamp():
    """Measured artifacts sit at 46-76%. A floor at or below that would pass
    documents we already know cite barely half of what they assert."""
    assert COVERAGE_FLOOR >= 80.0


# ===========================================================================
# THE PUBLISH DECISION
# ===========================================================================
# 🔴 Found 2026-08-09 by reading a page that had just been marked PUBLISHABLE.
# It carried NINE author notes, including two `[NEEDS PRICING]` on a page whose
# entire job is to state a price. Two independent failures produced that:
#
#   1. `find_placeholders` never ran on copy at publish time. `market.copy`
#      computed it per block and threw it away — `store_block` does not persist
#      it — so it was a line the CLI printed once and no gate ever saw.
#   2. The shouted `[NEEDS ...]` form was not a pattern. The lowercase rule
#      wanted the noun adjacent (`needs data`), so `[NEEDS TIMELINE DATA]`
#      slipped between the words.
#
# The decision now lives in `page_state`, which is pure, so these are testable
# without a database — the reason the rule could be wrong for so long.
from jarvis.market.pages import page_state


def _clean(**kw):
    return _data(**kw)


def test_a_clean_page_with_live_offers_is_publishable():
    st = page_state(_clean())
    assert st["publishable"] and st["blockers"] == []


def test_an_author_note_blocks_publication():
    st = page_state(_clean(body="Cut it to an hour [claim 4]. [NEEDS PRICING]"))
    assert not st["publishable"]
    assert st["placeholders"] == ["NEEDS marker"]
    assert any("unfinished work" in b for b in st["blockers"])


@pytest.mark.parametrize("marker", [
    "[NEEDS PRICING]",
    "[NEEDS DETAIL]",
    "[NEEDS TIMELINE DATA]",
    "[NEEDS DETAIL ON SELF-SERVICE FEATURE SET]",
    "[claim needed: which specific failure modes are covered]",
    "[claim would need checking: typical restoration timeline]",
])
def test_every_marker_found_on_the_real_page_now_blocks(marker):
    """The exact nine strings, deduped, from need-13's page."""
    st = page_state(_clean(body=f"Teams lose 12 hours [claim 4]. {marker}"))
    assert not st["publishable"], marker


def test_prose_that_merely_says_needs_is_not_an_author_note():
    """"this needs pricing before launch" is a sentence a buyer may read. Only
    the SHOUTED, bracketed form is the author talking to themselves."""
    st = page_state(_clean(body="Teams lose 12 hours [claim 4]. "
                                "The rollout needs pricing agreed first."))
    assert st["publishable"], st["blockers"]


def test_a_buyer_fill_in_field_still_does_not_block():
    """The 2026-08-09 `[your ...]` decision must survive this gate too."""
    st = page_state(_clean(body="Email [your billing descriptor] [claim 4]."))
    assert st["publishable"], st["blockers"]


def test_a_marker_in_ANY_tier_blocks_the_whole_page():
    d = _clean(tiers=("roadmap", "instructions", "deployed"))
    d["blocks"]["deployed"]["headline"]["body"] = "Ships in [NEEDS TIMELINE DATA]."
    assert not page_state(d)["publishable"]


def test_a_dead_offer_blocks_and_says_which_tier():
    d = _clean(tiers=("roadmap", "instructions"))
    d["offers"]["instructions"]["live"] = False
    st = page_state(d)
    assert not st["publishable"]
    assert any("instructions" in b for b in st["blockers"])


def test_the_blockers_are_reported_TOGETHER_not_one_at_a_time():
    """An operator fixing one thing at a time through three regenerations is
    how a launch slips a day. Report everything wrong at once."""
    d = _clean(body="Untraceable assertion about 40 firms losing 12 hours. "
                    "[NEEDS PRICING]")
    d["offers"]["roadmap"]["live"] = False
    st = page_state(d)
    assert len(st["blockers"]) >= 2


def test_publishable_is_exactly_the_absence_of_blockers():
    """No second code path deciding the same question differently."""
    for d in (_clean(), _clean(body="x [NEEDS PRICING]"), _clean(live=False)):
        st = page_state(d)
        assert st["publishable"] == (not st["blockers"])
