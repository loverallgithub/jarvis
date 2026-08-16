"""An absence is decided against the FULL BODY, not guessed from an excerpt.

WHY
───
`gap_analysis` produces claims of the form "No mention of X". Fact-checking
those against a 2,500-character excerpt asks the model whether something is
absent from a page it has only partly seen — and if the excerpt does not mention
X, that is equally consistent with "the page never does" and "the excerpt missed
it".

Measured across 2026-08-08/09 on need 13, the SAME claim set produced 3, 3, 2,
4, 2 and 1 unsupported over six identical runs, with the identity of the failing
claims changing each time. Claims 30, 34 and 36 were each hand-fixed after
failing; claim 36 had been PASSING minutes earlier with no input change.

Deciding it deterministically removes the variance AND the cost: no model call,
same answer every time.
"""
from __future__ import annotations

import pytest

from jarvis.forge.verify import (is_absence_claim, verify_absence,
                                 _topic_terms)


# ── recognising the shape ──────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "No mention of invoice OCR or document capture capabilities",
    "No discussion of integration depth or API capabilities",
    "The page lacks information about implementation timeline",
    "Lack of transparent pricing information",
    "No specific guidance on integrating with non-ERP systems",
    "Limited detail on payment automation capabilities",
    "Lacks detailed comparison of manual versus automated workflows",
    "Does not mention multi-currency support",
])
def test_absence_shaped_claims_are_recognised(text):
    assert is_absence_claim(text), text


@pytest.mark.parametrize("text", [
    "Tipalti is positioned for complex global payments at $99+/mo",
    "The page names SAP, Oracle, MS Dynamics and NetSuite",
    "Cardholders have 120 days to raise a chargeback",
])
def test_a_positive_claim_is_not_absence_shaped(text):
    assert not is_absence_claim(text), text


# ── the decision ───────────────────────────────────────────────────────────
def test_the_check_NEVER_refutes_only_confirms():
    """🔴 Refuting by term presence is the wrong instrument.

    majority rule   -> 9 of 14 claims wrongly refuted
    unanimity rule  -> 3 wrongly refuted, including "Lack of TRANSPARENT
                       pricing — vendors show 'NA - Custom quote'". Both
                       "transparent" and "pricing" are on the page, so presence
                       refutes it — but the claim is about the QUALITY of what
                       is shown, and its own evidence IS that table.

    Presence of a word is not coverage of an assertion. Refutation belongs to
    the model, which can read.
    """
    body = ("AP automation digitizes the invoice-to-payment cycle, capturing "
            "invoice documents with OCR and routing them for approval.")
    assert verify_absence("No mention of invoice OCR or document capture "
                          "capabilities", body) is not False


def test_a_topic_only_PARTLY_present_defers_to_the_model():
    """The page mentions invoices and capture but never documents. Two of three
    is not enough to delete a claim from a product."""
    body = ("AP automation digitizes the invoice cycle, capturing data with "
            "OCR and routing it for approval.")
    assert verify_absence("No mention of invoice OCR or document capture "
                          "capabilities", body) is None


def test_the_transparent_pricing_case_is_left_to_the_model():
    """Claim 31, the real false refutation: a claim about QUALITY, whose own
    evidence is the table the words appear in."""
    body = "Compare transparent pricing across vendors: Stampli NA - Custom quote"
    assert verify_absence("Lack of transparent pricing information across "
                          "vendors", body) is not False


def test_a_topic_genuinely_absent_confirms_the_gap():
    body = ("The platform routes approvals and syncs to your ledger in real "
            "time, with dashboards for spend and cycle time.")
    assert verify_absence("No mention of multi-currency or cross-border "
                          "payment support", body) is True


def test_prefix_matching_catches_inflections():
    """"capture" must find "capturing" — the exact miss that let claim 36
    assert an absence against a page discussing it."""
    assert verify_absence("No mention of document capture workflows",
                          "the tool is capturing documents in daily workflows") is None


def test_a_PARTIAL_match_is_UNDECIDED_not_a_refutation():
    """🔴 The bug that killed 9 of 14 claims in one run.

    A first version refuted on a MAJORITY. "The page does not provide specific
    pricing information or cost comparison" yields {provide, cost, comparison,
    pricing}; "provide" and "cost" appear in almost any prose, so two incidental
    hits refuted a gap that was real — the page contains neither "pricing" nor
    "price".

    Refuting takes unanimity; confirming takes silence; anything between goes to
    the model. A false refutation deletes a true claim from a product.
    """
    body = "The page discusses invoices at length but nothing else here."
    out = verify_absence("No mention of invoice OCR document capture "
                         "reconciliation workflows", body)
    assert out is None


def test_generic_verbs_are_not_topic_terms():
    """"provide", "discuss", "mention" occur in nearly all prose. Treating them
    as topic makes almost any page look like it covers almost anything."""
    assert _topic_terms("The page does not provide or discuss or mention") == []


def test_the_real_claim_27_is_not_refuted_by_incidental_words():
    """The exact production false refutation."""
    body = ("Automation reduces manual entry and can provide better visibility. "
            "The cost of delay is high for finance teams.")
    out = verify_absence(
        "The page does not provide specific pricing information or cost "
        "comparison across vendors", body)
    assert out is not False, "an incidental 'cost'/'provide' refuted a real gap"


def test_too_few_topic_terms_is_undecided():
    assert verify_absence("No mention of pricing", "anything at all") is None


def test_an_empty_body_is_undecided_rather_than_confirming_the_gap():
    """No body is not evidence of absence — it is absence of evidence."""
    assert verify_absence("No mention of invoice OCR and capture tools", "") is None


# ── topic extraction ───────────────────────────────────────────────────────
def test_the_topic_stops_at_the_explanatory_clause():
    """"No mention of X - the page only shows Y" — Y is commentary, not topic.
    Including it would search for words the claim never asserted were missing."""
    terms = _topic_terms("No mention of multi-currency or cross-border payment "
                         "capabilities - the page only highlights Tipalti")
    assert "currency" in " ".join(terms)
    assert "tipalti" not in terms


def test_filler_nouns_are_not_topic_terms():
    """"page", "capabilities", "information" appear in every gap claim and in
    every page; matching on them would refute everything."""
    terms = _topic_terms("No mention of the page capabilities information detail")
    assert terms == []


def test_the_check_is_deterministic():
    body = "capturing invoices with OCR and routing them for approval"
    claim = "No mention of invoice OCR or document capture capabilities"
    assert verify_absence(claim, body) == verify_absence(claim, body)
