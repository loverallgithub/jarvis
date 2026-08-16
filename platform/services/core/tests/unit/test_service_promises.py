"""Copy sells a DOCUMENT. It must never promise that the seller will act.

WHAT HAPPENED, 2026-08-09
─────────────────────────
`TIER_BUYER` described the deployed tier as "a built, configured, tested,
handed-over system" — the tier LADDER's generic definition. The copy generator
read that as a service and wrote:

    "We restore your login or document the service failure"
    "We can get you access restored within 48 hours"
    "Most cases resolve within [X business days — needs data]"

None of that can be cited, and no evidence ever could: the evidence is App Store
reviews of someone else's broken app. `benefits` and `faq` failed the coverage
floor on ALL THREE tiers while `headline`, `subhead` and `objections` mostly
passed — because the first two described a SERVICE and the rest described the
PROBLEM.

WHY A SEPARATE CHECK RATHER THAN LEANING ON COVERAGE
────────────────────────────────────────────────────
Coverage catches "we restore your login within 48 hours" only because it
contains a checkable number. It does NOT catch "We'll handle it" — no number, no
measurement vocabulary, nothing checkable, so it sails through a coverage gate
while being the most dangerous sentence on the page. A promise with no facts in
it is still a promise.
"""
from __future__ import annotations

import pytest

from jarvis.market.copy import DELIVERABLE, TIER_BUYER, service_promises


# ── the observed sentences ─────────────────────────────────────────────────
@pytest.mark.parametrize("sent", [
    "We restore your login or document the service failure.",
    "We can get you access restored within 48 hours.",
    "We will handle the escalation for you.",
    "We'll contact the vendor on your behalf.",
    "Our team monitors the ticket until it closes.",
    "This is a done-for-you service.",
    "You get a dedicated support contact.",
    "We provide written documentation of the failure.",
    "We guarantee a refund if it does not work.",
])
def test_a_service_promise_is_caught(sent):
    assert service_promises(sent), f"missed: {sent!r}"


def test_a_promise_with_NO_facts_is_still_caught():
    """The case coverage cannot see: nothing checkable, maximum danger."""
    from jarvis.forge.verify import citation_coverage
    s = "We'll handle it."
    assert citation_coverage(s)["checkable"] == 0      # invisible to coverage
    assert service_promises(s)                          # visible here


# ── legitimate document copy passes ────────────────────────────────────────
@pytest.mark.parametrize("sent", [
    "The document gives you the exact escalation sequence, with wording for each step.",
    "You send the cancellation notice yourself, using the template on page 12.",
    "It lists the four ERP integrations the vendor publishes [claim 34].",
    "Readers get a chargeback timeline they can check against their issuer's rules.",
    "We researched 40 vendor pages to build this.",   # past tense, not a promise
])
def test_document_language_is_not_flagged(sent):
    assert service_promises(sent) == [], f"wrongly flagged: {sent!r}"


def test_examples_are_capped_so_the_report_stays_readable():
    text = " ".join(["We will restore your access."] * 20)
    assert len(service_promises(text)) == 5


def test_empty_input_is_safe():
    assert service_promises("") == []
    assert service_promises(None) == []          # type: ignore[arg-type]


# ── the framing that prevents it at source ─────────────────────────────────
def test_the_prompt_states_the_product_is_a_document():
    assert "DOCUMENT" in DELIVERABLE
    assert "no service" in DELIVERABLE.lower()


def test_no_tier_is_described_as_something_someone_else_operates():
    """The deployed tier's old wording — "built, configured, tested,
    handed-over system" — is what produced the service copy."""
    for tier, desc in TIER_BUYER.items():
        low = desc.lower()
        assert "handed-over system" not in low, tier
        assert "document" in low or "manual" in low, tier


# ── a denial is not a promise ──────────────────────────────────────────────
# The FIXED prompt made the copy say exactly the right thing, and the first
# version of this checker flagged all of it. These are the real sentences it
# wrongly rejected on 2026-08-09.
@pytest.mark.parametrize("sent", [
    "This document is a manual you execute yourself—there's no support desk, "
    "no account team, nobody acting for you.",
    "There is no account team, support ticket, or anyone acting on your behalf.",
    "You follow the document yourself—there's no support team, no account "
    "manager, and nothing we do for you.",
    "The document does not contact the vendor for you.",
    "We will not escalate on your behalf; you send the emails.",
    "Nobody restores your login but you.",
    "This is not a done-for-you service.",
])
def test_a_DENIAL_of_service_is_not_flagged(sent):
    assert service_promises(sent) == [], f"wrongly flagged a denial: {sent!r}"


def test_a_real_promise_after_a_negation_elsewhere_is_still_caught():
    """The negation must guard the MATCH, not license the whole sentence."""
    sent = ("There is no setup fee, and we restore your login within 48 hours.")
    assert service_promises(sent), "a real promise slipped through behind a negation"


def test_the_negation_window_does_not_reach_across_a_whole_paragraph():
    """A 'no' two hundred characters earlier says nothing about this clause."""
    sent = ("No refunds are offered on digital goods once the file has been "
            "downloaded and opened by the purchaser under the usual terms that "
            "apply to this category of product in most jurisdictions, and after "
            "all of that we handle the escalation for you.")
    assert service_promises(sent)
