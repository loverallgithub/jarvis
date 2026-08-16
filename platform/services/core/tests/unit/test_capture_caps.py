"""The two capture caps, and the invariant that binds them.

Per lesson 60, tunable DATA and constants are exactly where behaviour changes
without a test noticing. These constants decide whether a claim CAN be verified
at all, so the invariants are asserted here rather than left to a live run.

Measured 2026-08-08 on need 13:
  ramp.com          1,448,182 bytes  → strips to 42,503 chars
  mhcautomation.com   753,559 bytes  → 67 chars at a 400 KB cap (REJECTED),
                                        61,874 chars once the cap clears it
  highradius.com      356,039 bytes  → 9,651 chars
"""
from __future__ import annotations

from jarvis.research import evidence as ev


def test_max_bytes_clears_the_real_pages_that_were_being_truncated():
    """753,559 is the observed mhcautomation size. A cap below it put the whole
    article outside the capture and left a stylesheet behind."""
    assert ev.MAX_BYTES >= 800_000


def test_body_chars_is_large_enough_to_hold_a_stripped_article():
    """42,503 is ramp's stripped length. The fact-checker only ever sees this
    field, so an article longer than the cap is partly unverifiable."""
    assert ev.BODY_CHARS >= 45_000


def test_the_body_cap_is_not_larger_than_what_can_be_fetched():
    """A BODY_CHARS above MAX_BYTES would be a promise the fetch cannot keep and
    would read as 'nothing was truncated' when things were."""
    assert ev.BODY_CHARS <= ev.MAX_BYTES


def test_min_body_stays_far_below_the_body_cap():
    """The substantive floor and the storage ceiling must not converge, or the
    gate stops discriminating."""
    assert ev.MIN_BODY_CHARS < ev.BODY_CHARS / 10


def test_a_truncated_stylesheet_page_is_still_rejected_after_the_raise():
    """Raising the cap must not weaken the gate: a page that is STILL cut inside
    a style block must still be rejected, not admitted as CSS."""
    css = "a{color:red}" * 5000
    body = ev._strip_html(f"<html><head><title>T</title><style>{css}")
    cap = ev.Captured(url="https://e.com/x", sha256="a" * 64, http_status=200,
                      live=True, title="T", body=body, mime="text/html",
                      bytes=ev.MAX_BYTES)
    assert cap.substantive is False


def test_a_real_article_under_the_caps_is_admitted():
    """The other direction — the gate must not become so strict it rejects
    everything, which would look identical to 'no evidence found'."""
    text = ("<html><head><title>AP</title></head><body><p>"
            + ("Accounts payable teams lose hours to manual invoice entry. " * 40)
            + "</p></body></html>")
    cap = ev.Captured(url="https://e.com/a", sha256="b" * 64, http_status=200,
                      live=True, title="AP", body=ev._strip_html(text),
                      mime="text/html", bytes=4000)
    assert cap.substantive is True
    assert cap.reject_reason is None
