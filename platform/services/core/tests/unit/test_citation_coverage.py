"""Citation coverage — the check "zero uncited claims" was never making.

`claims.evidence_id` is NOT NULL, so `uncited_claims` could only ever be 0: a
column constraint reported as a verification result, in every checkpoint, as an
achievement. What the phrase sounds like it means is measured here — does each
assertion a reader could CHECK carry a `[claim N]` marker?

First real measurement (2026-08-09, need 13): roadmap 76.2%, instructions 51.3%,
deployed 46.7% — 130 of 231 checkable assertions cited, 56.3% overall.

The metric is deliberately CONSERVATIVE and directional rather than exact. It
counts numbers, money, percentages and the vocabulary of measurement. It will
flag some planning figures that are choices rather than claims ("Duration: 3
days") — so treat it as a floor on the problem, not a precise score. The
alternative, flagging every sentence, buries real misses in noise and teaches
the reader to ignore the number.
"""
from __future__ import annotations

import pytest

from jarvis.forge.verify import (citation_coverage,
                                 is_product_self_description)


def test_a_cited_statistic_counts_as_covered():
    c = citation_coverage("Teams lose 12 hours a week to manual entry [claim 4].")
    assert c["checkable"] == 1 and c["cited"] == 1
    assert c["coverage_pct"] == 100.0


def test_an_uncited_statistic_is_caught():
    c = citation_coverage("Teams lose 12 hours a week to manual entry.")
    assert c["checkable"] == 1 and c["cited"] == 0
    assert c["coverage_pct"] == 0.0
    assert c["examples"] and "12 hours" in c["examples"][0]


def test_prose_that_asserts_nothing_is_not_counted():
    """Instructions and framing need no citation. Flagging them would bury the
    real misses and train the reader to ignore the metric."""
    c = citation_coverage("Open the billing page and choose Cancel subscription.")
    assert c["checkable"] == 0
    assert c["coverage_pct"] == 100.0


def test_money_and_percentages_are_checkable():
    for s in ("The tool costs $15 per user.", "Adoption rose 40% year on year."):
        assert citation_coverage(s)["checkable"] == 1, s


def test_the_vocabulary_of_measurement_is_checkable_without_a_number():
    for s in ("According to industry benchmarks the process is slower.",
              "Most vendors decline to publish a price."):
        assert citation_coverage(s)["checkable"] == 1, s


def test_a_bullet_citing_once_covers_its_elaboration():
    """A bullet often cites once then elaborates in the same line; splitting it
    into sentences and demanding a marker on each would be a false miss."""
    line = ("- Vendors hide pricing [claim 9]. Most publish no rate card at all "
            "and 3 of 5 require a sales call.")
    c = citation_coverage(line)
    assert c["cited"] == c["checkable"] and c["checkable"] >= 1


def test_the_generated_Sources_block_is_excluded():
    """It is nothing but citations and would flatter the number."""
    doc = ("Teams lose 12 hours a week.\n\n---\n\n## Sources\n\n"
           "- **[claim 4]** something measured, 40% of 900 firms\n")
    c = citation_coverage(doc)
    assert c["checkable"] == 1 and c["cited"] == 0


def test_fenced_code_is_excluded():
    doc = "Run it.\n```\nexport LIMIT=4000  # 90% of quota\n```\nDone.\n"
    assert citation_coverage(doc)["checkable"] == 0


def test_headings_and_table_rules_are_skipped():
    doc = "## 5 Steps To 90% Faster Close\n|---|---|\n"
    assert citation_coverage(doc)["checkable"] == 0


def test_a_document_with_nothing_checkable_scores_100_not_0():
    """Zero-of-zero must not read as total failure — it means there was nothing
    to cite, which is a different thing from citing nothing."""
    c = citation_coverage("This guide explains how to proceed.")
    assert c["coverage_pct"] == 100.0


def test_examples_are_capped_so_the_report_stays_readable():
    doc = "\n".join(f"Item {i}: teams lose {i+10} hours each week." for i in range(40))
    c = citation_coverage(doc)
    assert c["checkable"] >= 20
    assert len(c["examples"]) == 5


def test_empty_and_none_are_safe():
    for v in ("", None):
        c = citation_coverage(v)  # type: ignore[arg-type]
        assert c["checkable"] == 0 and c["coverage_pct"] == 100.0


def test_mixed_document_reports_a_partial_score():
    doc = ("Teams lose 12 hours a week [claim 1].\n"
           "Vendors charge $99 per month.\n"
           "Open the settings page.\n")
    c = citation_coverage(doc)
    assert c["checkable"] == 2 and c["cited"] == 1
    assert c["coverage_pct"] == 50.0


# ── product self-descriptions are out of scope ─────────────────────────────
# Operator decision, 2026-08-09. "The document is approximately 40-50 pages" is
# checkable in principle and citable only against the artifact itself, never
# against the research. Counting it as an UNCITED claim asked the copy to cite a
# source that could not exist, and six copy blocks sat below the floor on
# exactly this.
#
# Excluded from the DENOMINATOR, not counted as cited — counting them as cited
# would inflate coverage with sentences nobody checked.
@pytest.mark.parametrize("sent", [
    "The document is approximately 40-50 pages depending on your account type.",
    "This guide is structured to be completed in one sitting, typically 20-40 minutes.",
    "The manual runs to 12 sections covering each escalation route.",
    "You will receive a 30-page playbook and 6 templates.",
])
def test_a_product_self_description_is_recognised(sent):
    assert is_product_self_description(sent), sent


@pytest.mark.parametrize("sent", [
    # a world claim, even though the subject is the document
    "The document proves 40% of vendors hide pricing behind a demo call.",
    # a world claim with no deliverable subject at all
    "Teams lose 12 hours a week to manual invoice entry.",
    # a deliverable subject with no product metric
    "The document explains what to send and in what order.",
])
def test_a_world_claim_is_NOT_excluded(sent):
    assert not is_product_self_description(sent), sent


def test_self_descriptions_leave_the_denominator_rather_than_counting_as_cited():
    """The distinction that matters: coverage must not be inflated by sentences
    nobody checked. 100% of one real claim, not 100% of two."""
    doc = ("The document is approximately 40-50 pages.\n"
           "Teams lose 12 hours a week to manual entry [claim 4].\n")
    c = citation_coverage(doc)
    assert c["checkable"] == 1
    assert c["cited"] == 1
    assert c["self_described"] == 1
    assert c["coverage_pct"] == 100.0


def test_an_uncited_world_claim_still_fails_beside_a_self_description():
    doc = ("This guide is 40 pages long.\n"
           "Vendors charge $99 per month.\n")
    c = citation_coverage(doc)
    assert c["checkable"] == 1 and c["cited"] == 0
    assert c["coverage_pct"] == 0.0
    assert c["self_described"] == 1


def test_the_exclusion_cannot_be_used_as_a_loophole():
    """Mentioning "the document" must not launder a claim about the world."""
    doc = "The document shows that 73% of vendors never respond to support tickets."
    c = citation_coverage(doc)
    assert c["checkable"] == 1, "a world claim was excluded by naming the document"
    assert c["self_described"] == 0


# ── domain nouns are not measurement vocabulary ────────────────────────────
# Operator decision, 2026-08-09. `vendors?` and `competitors?` were in the
# checkable list and flagged table-of-contents lines that assert nothing:
#
#   "A decision tree for your next move — whether to pursue a chargeback,
#    switch vendors, or decide"                    <- trigger was 'vendors'
#   "Whether access is restored depends on your vendor's systems"
#
# Everything remaining signals MEASUREMENT or ATTRIBUTION. A domain noun signals
# subject matter, and subject matter is not a claim.
@pytest.mark.parametrize("sent", [
    "A decision tree for your next move — whether to pursue a chargeback, "
    "switch vendors, or decide.",
    "Whether access is restored depends on your vendor's systems responding.",
    "The guide compares competitors side by side so you can choose.",
])
def test_a_bare_domain_noun_is_not_checkable(sent):
    assert citation_coverage(sent)["checkable"] == 0, sent


@pytest.mark.parametrize("sent", [
    "Most vendors decline to publish a price.",              # 'most'
    "40% of vendors hide pricing behind a demo call.",       # '40%'
    "3 of 5 vendors require a sales call before quoting.",   # the number
    "According to the survey, vendors rarely publish rates.",  # 'according to'
])
def test_a_REAL_claim_about_vendors_is_still_checkable(sent):
    """Nothing is lost: the quantifier was always doing the work, not the noun."""
    assert citation_coverage(sent)["checkable"] == 1, sent


def test_removing_the_noun_did_not_gut_the_vocabulary():
    """A list that flags nothing is indistinguishable from a deleted check."""
    for sent in ("The study found a median of 12 hours lost per week.",
                 "Typically the close takes longer than reported.",
                 "According to industry benchmarks it is slower."):
        assert citation_coverage(sent)["checkable"] == 1, sent


# ── the offer-description carve-out ────────────────────────────────────────
# Operator decision, 2026-08-16. Run 24 failed its floor on two sentences the
# research corpus can never cite: who the product is FOR and what it COSTS.
# Both describe the OFFER, not the world — the offer did not exist when the
# evidence was captured. Same accounting as the deliverable carve-out:
# excluded from the denominator, never counted as cited.
#
# The price arm is STRICTER than the citation it replaces: an amount is carved
# out only when it matches offers.price_minor, passed in by the caller — a
# wrong price still fails.

def test_an_audience_targeting_sentence_is_out_of_scope():
    c = citation_coverage(
        "This is for owner-operators and small business owners (1–15 employees, "
        "no dedicated finance staff) who are locked out of an AP platform.")
    assert c["checkable"] == 0
    assert c["offer_described"] == 1


def test_a_price_matching_the_offer_is_out_of_scope():
    c = citation_coverage(
        "The full manual costs €40 and is a one-time purchase.",
        offer_prices_minor=frozenset({4000}))
    assert c["checkable"] == 0
    assert c["offer_described"] == 1


def test_a_price_with_no_offer_passed_still_counts():
    """The carve-out is opt-in per call site. The forge's artifact path passes
    no prices, so nothing there changes behaviour by accident."""
    c = citation_coverage("The full manual costs €40 and is a one-time purchase.")
    assert c["checkable"] == 1 and c["cited"] == 0


def test_a_WRONG_price_is_never_carved_out():
    """The mechanical check is stricter than the citation it replaces — copy
    claiming a price the checkout will not honour still fails."""
    c = citation_coverage(
        "The full manual costs €45 and is a one-time purchase.",
        offer_prices_minor=frozenset({4000}))
    assert c["checkable"] == 1 and c["cited"] == 0


def test_a_world_claim_survives_offer_prices_being_passed():
    c = citation_coverage("Most vendors decline to publish a price.",
                          offer_prices_minor=frozenset({4000}))
    assert c["checkable"] == 1


def test_a_decimal_price_matches_its_minor_units():
    c = citation_coverage("You pay €3.50 once — a one-time purchase.",
                          offer_prices_minor=frozenset({350}))
    assert c["checkable"] == 0
    assert c["offer_described"] == 1
