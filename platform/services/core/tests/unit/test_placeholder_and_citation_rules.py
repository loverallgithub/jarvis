"""Two rule changes made on 2026-08-09, and the risk each one accepts.

────────────────────────────────────────────────────────────────────────────
1. `[your ...]` IS A BUYER FIELD, NOT UNFINISHED WORK   (operator decision)
────────────────────────────────────────────────────────────────────────────
The placeholder rule conflated two things that share a notation:

    [your billing descriptor]   a field THE BUYER fills in. The instructions
                                and deployed tiers hand the buyer ready-made
                                emails to send their vendor; a slot for their
                                own reference is the POINT of the template.
    [insert vendor name]        work the AUTHOR did not finish.

Artifacts #7 and #8 were withheld from sale solely because of
`[your billing descriptor]` and `[your account email]` — the rule was rejecting
the deliverable for doing its job.

Accepted risk, knowingly: lorem of the form "[your name here]" now passes. No
current artifact contains anything of that shape; the only `[your ...]` tokens
across all three are billing descriptor, account email and billing email.

────────────────────────────────────────────────────────────────────────────
2. A MISSPELLED CITATION IS STILL A CITATION
────────────────────────────────────────────────────────────────────────────
Found in the need-13 artifacts: `[claip 33]` and `[claik 28]`. The model reached
for a citation and typed it wrong by one letter. The strict `\\[claim N\\]`
pattern did not match, so two GENUINE citations were invisible to coverage AND
dropped from the Sources block — a claim silently losing its evidence.
"""
from __future__ import annotations

import pytest

from jarvis.forge.build import find_placeholders
from jarvis.forge.verify import citation_coverage


# ── buyer fields pass ──────────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "Subject: Account lockout — [your billing descriptor]",
    "Reply-to: [your account email]",
    "Send it from [your billing email] so the vendor can match the account.",
])
def test_a_buyer_fill_in_field_is_not_a_placeholder(text):
    assert find_placeholders(text) == []


# ── unfinished work is still caught ────────────────────────────────────────
@pytest.mark.parametrize("text,label", [
    ("Contact [insert vendor name] before day 7.", "bracketed placeholder"),
    ("The price is [placeholder].", "bracketed placeholder"),
    ("Use [xxx] as the reference.", "bracketed placeholder"),
    ("Timeline: TBD", "TBD"),
    ("Cost: XXXX per month", "XXXX filler"),
    ("This section is PLACEHOLDER", "shouted PLACEHOLDER"),
    ("lorem ipsum dolor sit amet", "lorem ipsum"),
    ("Full details coming soon.", "coming soon"),
])
def test_unfinished_work_is_still_flagged(text, label):
    assert label in find_placeholders(text)


def test_the_narrowing_did_not_disable_the_whole_rule():
    """The change removed ONE alternative, not the check. A rule that flags
    nothing is indistinguishable from a rule that was deleted."""
    assert find_placeholders("Contact [insert name] and pay [placeholder]")


def test_a_document_mixing_both_flags_only_the_unfinished_one():
    doc = ("Email [your billing descriptor] to the vendor. "
           "Escalate to [insert escalation contact] after 7 days.")
    assert find_placeholders(doc) == ["bracketed placeholder"]


# ── citations survive a typo ───────────────────────────────────────────────
@pytest.mark.parametrize("marker", ["[claim 4]", "[claip 4]", "[claik 4]",
                                    "[Claim 4]", "[CLAIM 4]"])
def test_a_misspelled_citation_still_counts_as_cited(marker):
    c = citation_coverage(f"Teams lose 12 hours a week to manual entry {marker}.")
    assert c["checkable"] == 1 and c["cited"] == 1, marker


def test_the_tolerance_is_not_a_wildcard():
    """`cla` + two letters + a number. Ordinary bracketed prose must not be
    mistaken for a citation, or coverage becomes meaningless."""
    for text in ("Teams lose 12 hours [see appendix 4].",
                 "Teams lose 12 hours [table 4].",
                 "Teams lose 12 hours [4]."):
        c = citation_coverage(text)
        assert c["cited"] == 0, text


def test_the_real_artifact_typos_are_recovered():
    """The exact strings found in the need-13 artifacts."""
    from jarvis.forge.build import GeneratedSection, render  # noqa: F401
    import re
    from jarvis.market.copy import _cited_ids
    assert _cited_ids("supported by [claip 33] and [claik 28] and [claim 7]") \
        == [7, 28, 33]


def test_cited_ids_ignores_things_that_are_not_citations():
    from jarvis.market.copy import _cited_ids
    assert _cited_ids("see [table 3] and [appendix 9]") == []


# ── unfinished-work markers found in SHIPPABLE SALES COPY ──────────────────
# 2026-08-09: five markers across three copy blocks, every one of which passed
# the coverage gate. An artifact carrying `[insert vendor name]` is withheld
# from sale; copy carrying `[Price would go here]` was shipping to buyers.
@pytest.mark.parametrize("text,label", [
    ("[Price would go here]. This is a one-time purchase.", "would-go-here marker"),
    ("covers the failure points [claim needed: which modes]", "claim-needed marker"),
    ("Most cases resolve within [X business days — needs data].", "needs-data marker"),
    ("the figure is [citation needed] at this point", "claim-needed marker"),
    ("timeline [data needed] before launch", "claim-needed marker"),
])
def test_author_notes_in_copy_are_caught(text, label):
    assert label in find_placeholders(text), text


def test_the_new_patterns_do_not_flag_buyer_fields():
    """The `[your ...]` decision must survive these additions."""
    assert find_placeholders("Subject: cancellation — [your billing descriptor]") == []


def test_a_bracket_that_is_not_an_author_note_is_left_alone():
    for ok in ("the schedule [Monday to Friday] applies",
               "see the appendix [section 4] for detail",
               "escalate to the issuer [within 60 days]"):
        assert find_placeholders(ok) == [], ok
