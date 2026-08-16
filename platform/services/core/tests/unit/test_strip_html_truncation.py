"""A page truncated inside a <style> block must be REJECTED, never cited.

Regression cover for claim 28 of need 13. `mhcautomation.com` is 753,559 bytes;
`MAX_BYTES` cuts the fetch at 400,000 — inside a single huge inlined stylesheet.
The captured bytes therefore held one `<style>` and zero `</style>`, the
paired-tag regex could not match, and 8,000 characters of CSS were stored as
`body` with `substantive = true`.

The fact-checker was later handed that CSS and replied "only HTML/CSS formatting
code with no actual content" — a true statement that pointed nowhere near the
byte cap that caused it, and an artifact was withheld over it.

The contract these tests pin: an unclosed script/style runs to end-of-text, so
what survives is real text or nothing at all.
"""
from __future__ import annotations

from jarvis.research.evidence import MIN_BODY_CHARS, Captured, _strip_html

CSS = "img:is([sizes=auto i],[sizes^='auto,' i]){contain-intrinsic-size:3000px 1500px}" * 60


def _truncated_page() -> str:
    """Exactly the observed shape: <title>, an opened <style>, no closer."""
    return f"<html><head><title>15 Common AP Challenges</title><style>{CSS}"


def test_an_unclosed_style_block_is_stripped_to_the_end():
    out = _strip_html(_truncated_page())
    assert "contain-intrinsic-size" not in out
    assert "sizes=auto" not in out
    assert "15 Common AP Challenges" in out


def test_an_unclosed_script_block_is_stripped_to_the_end():
    out = _strip_html("<html><body><p>Real text.</p><script>var a = {x: 1};" * 1)
    assert "var a" not in out
    assert "Real text." in out


def test_the_truncated_page_is_now_too_thin_to_be_substantive():
    """The point of the fix: it becomes a REJECTED page, not a cited stylesheet."""
    cap = Captured(url="https://example.com/post", sha256="a" * 64, http_status=200,
                   live=True, title="15 Common AP Challenges",
                   body=_strip_html(_truncated_page()), mime="text/html",
                   bytes=400_000)
    assert len(cap.body) < MIN_BODY_CHARS
    assert cap.substantive is False
    assert "too thin" in (cap.reject_reason or "")


def test_the_old_paired_tag_behaviour_still_works():
    out = _strip_html("<style>.a{color:red}</style><p>Body text here.</p>")
    assert "color:red" not in out
    assert "Body text here." in out


def test_a_closing_tag_with_whitespace_still_matches_as_a_pair():
    out = _strip_html("<script>evil()</script >\n<p>Kept.</p>")
    assert "evil()" not in out
    assert "Kept." in out


def test_several_closed_blocks_then_one_unclosed():
    html = ("<style>.a{}</style><p>First.</p>"
            "<script>ok()</script><p>Second.</p>"
            f"<style>{CSS}")
    out = _strip_html(html)
    assert "First." in out and "Second." in out
    assert "contain-intrinsic-size" not in out


def test_real_content_before_the_unclosed_block_survives():
    """Truncation must not cost us the article text that WAS captured."""
    html = ("<p>Accounts payable teams lose 12 hours a week to manual entry.</p>"
            f"<style>{CSS}")
    out = _strip_html(html)
    assert "lose 12 hours a week" in out
    assert "contain-intrinsic-size" not in out


def test_the_word_style_in_prose_is_not_treated_as_a_tag():
    out = _strip_html("<p>Choose a style of invoice approval that fits.</p>")
    assert "style of invoice approval" in out


def test_empty_and_none_are_safe():
    assert _strip_html("") == ""
    assert _strip_html(None) == ""      # type: ignore[arg-type]
