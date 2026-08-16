"""The plan owns the section heading — the model does not.

Regression cover for a real withheld artifact. On 2026-08-08 the `deployed`
tier of need 13 came back with `# Who This Is For` instead of
`## Who This Is For`. Every word of the section was present and correct, but
`structural()` matches on the literal `## <heading>`, so it reported the
section MISSING, `structural_ok` went False and the artifact was withheld —
over one character, after paying to generate all eight sections.

These tests pin the normalisation. They are unit tests on purpose: this must
be provable without an LLM, because the failure only ever appears at $6 a run.
"""
from __future__ import annotations

import pytest

from jarvis.forge.build import GeneratedSection, section_body


def _sec(text: str, heading: str = "Who This Is For") -> GeneratedSection:
    return GeneratedSection(key="audience", heading=heading, text=text,
                            words=len(text.split()), claim_ids=[])


def test_the_observed_h1_failure_is_normalised_to_h2():
    """The exact bug: one `#` instead of two."""
    out = section_body(_sec("# Who This Is For\n\nSMB operators drowning in AP."))
    assert out.startswith("## Who This Is For")
    assert "SMB operators drowning in AP." in out
    # and the malformed original must not survive alongside the fixed one
    assert "\n# Who This Is For" not in out


def test_a_correct_heading_is_left_as_one_heading_not_duplicated():
    out = section_body(_sec("## Who This Is For\n\nBookkeepers at 5–50 seat firms."))
    assert out.count("Who This Is For") == 1
    assert out.startswith("## Who This Is For")


@pytest.mark.parametrize("level", ["#", "###", "####", "#####", "######"])
def test_every_heading_level_is_normalised(level):
    out = section_body(_sec(f"{level} Who This Is For\n\nBody text here."))
    assert out.startswith("## Who This Is For")
    assert "Body text here." in out


def test_a_section_with_no_heading_at_all_still_gets_one():
    """The other half of the contract — a missing heading is as fatal as a wrong one."""
    out = section_body(_sec("SMB operators, 5-50 staff, no AP clerk."))
    assert out.startswith("## Who This Is For\n\n")
    assert "SMB operators, 5-50 staff, no AP clerk." in out


def test_a_trailing_colon_or_suffix_still_counts_as_the_heading():
    out = section_body(_sec("## Who This Is For: the AP clerk\n\nBody."))
    assert out.startswith("## Who This Is For\n\n")
    assert out.count("##") == 1


def test_a_first_line_heading_that_says_something_else_is_CONTENT_and_survives():
    """Do not eat a real subheading.

    Stripping any leading '#' line unconditionally would silently delete
    content, which is a worse failure than the one being fixed: it would be
    invisible in the artifact rather than caught by structural().
    """
    out = section_body(_sec("### Selection rules\n\nApplied before comparison."))
    assert out.startswith("## Who This Is For")
    assert "### Selection rules" in out
    assert "Applied before comparison." in out


def test_case_and_spacing_differences_still_match():
    out = section_body(_sec("#   who   this   is   FOR\n\nBody."))
    assert out.startswith("## Who This Is For\n\n")
    assert out.count("who   this") == 0


def test_an_empty_body_yields_a_bare_heading_and_never_crashes():
    out = section_body(_sec(""))
    assert out == "## Who This Is For"


def test_the_result_is_what_structural_greps_for():
    """Ties the fix to the check it exists to satisfy.

    structural() does `f"## {s.heading}".lower() not in text.lower()`.
    """
    for raw in ("# Who This Is For\n\nBody.",
                "## Who This Is For\n\nBody.",
                "Body with no heading."):
        out = section_body(_sec(raw))
        assert "## who this is for" in out.lower()


# ── sub-headings must not truncate the section ─────────────────────────────
def test_a_model_written_h2_inside_a_section_is_demoted():
    """🔴 The bug that withheld a 598-word section as "thin (4 words)".

    `structural()` measures a section by splitting on `\\n## `, so a model
    sub-heading at the same level ENDS the section for measurement purposes.
    The roadmap tier was withheld from sale over a document that was never thin.
    """
    s = _sec("## Effort, Cost & Confidence\n\n"
             "## What the pricing evidence supports\n\nBody text here.",
             heading="Effort, Cost & Confidence")
    out = section_body(s)
    assert out.startswith("## Effort, Cost & Confidence")
    assert "\n## What the pricing" not in out
    assert "### What the pricing evidence supports" in out


def test_a_single_hash_subheading_is_also_demoted():
    s = _sec("# A Sub Heading\n\nBody.", heading="Risk Register")
    out = section_body(s)
    assert out.count("\n## ") == 0
    assert "### A Sub Heading" in out


def test_h3_and_deeper_are_left_alone():
    """Only `#`/`##` collide with the plan's level."""
    s = _sec("### Already fine\n\n#### Deeper\n\nBody.", heading="Risk Register")
    out = section_body(s)
    assert "### Already fine" in out
    assert "#### Deeper" in out


def test_the_section_survives_the_split_that_structural_uses():
    """End to end on the real failure: split the rendered section the way
    structural() does and confirm the word count is the BODY, not the title."""
    import re
    body = "## Sub One\n\n" + ("word " * 300) + "\n\n## Sub Two\n\n" + ("word " * 300)
    # Rendered as it appears in a real document — preceded by another section,
    # so the `\n## ` that structural() splits on is actually present. Splitting
    # a standalone section finds no leading newline and matches nothing.
    doc = "# Title\n\n## The Outcome\n\nEarlier section.\n\n" + \
        section_body(_sec(body, heading="Effort, Cost & Confidence"))
    chunks = re.split(r"\n##\s+", doc)
    chunk = next(c for c in chunks
                 if c.lower().startswith("effort, cost & confidence"))
    assert len(chunk.split()) > 500, "the sub-heading truncated the section again"


def test_a_hash_inside_prose_is_not_a_heading():
    """`#1` in running text must not be demoted — only line-leading markers."""
    s = _sec("Ticket #1 was closed and issue #2 remains open.",
             heading="Risk Register")
    assert "Ticket #1" in section_body(s)
