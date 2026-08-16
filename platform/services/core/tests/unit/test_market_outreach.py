"""F5b — outreach to the people who described the problem.

This is the most dangerous step in the pipeline. Those people are in the
database because they posted on Reddit or filed a GitHub issue, NOT because
they asked to hear from us. `02-ARCHITECTURE.md` makes the rules
non-negotiable: `do_not_contact` defaults TRUE for anything community-scraped,
promotion requires a lawful basis recorded per voice, and outreach is opt-in per
launch.

The property these tests exist to pin is that the step **fails rather than
filters**. Silently dropping the non-compliant candidates is the tempting design
and it is wrong: it turns a compliance stop into a smaller send that looks
successful, and nobody ever learns the list was unusable.

Measured 2026-08-09: 218 voice mentions on this platform, 0 contactable.
"""
from __future__ import annotations

import pytest

from jarvis.market import outreach


def _plan(eligible=0, blocked=0, excluded=0, total=None):
    return {
        "need_id": 13,
        "eligible": [{"voice_id": i} for i in range(eligible)],
        "blocked": [{"voice_id": 100 + i, "name": f"v{i}", "why": "no lawful basis"}
                    for i in range(blocked)],
        "excluded": [{"voice_id": 200 + i, "why": "competitor"} for i in range(excluded)],
        "total_voices": total if total is not None else eligible + blocked + excluded,
    }


# ── the refusal ────────────────────────────────────────────────────────────
def test_one_blocked_recipient_refuses_the_WHOLE_launch():
    """Not "send to the 9 who passed". That is the failure mode this prevents."""
    with pytest.raises(PermissionError) as e:
        outreach.assert_sendable(_plan(eligible=9, blocked=1))
    assert "REFUSING" in str(e.value)


def test_the_refusal_names_who_and_why():
    """A refusal you cannot act on is just an outage."""
    with pytest.raises(PermissionError) as e:
        outreach.assert_sendable(_plan(eligible=2, blocked=1))
    msg = str(e.value)
    assert "voice 100" in msg and "no lawful basis" in msg


def test_an_empty_eligible_list_is_refused_and_called_expected():
    """0 contactable is the CORRECT state for a community-scraped audience, and
    the message must say so — otherwise someone 'fixes' it by relaxing consent."""
    with pytest.raises(PermissionError) as e:
        outreach.assert_sendable(_plan(eligible=0, blocked=0, excluded=3))
    assert "not a bug" in str(e.value)


def test_a_fully_compliant_list_passes():
    outreach.assert_sendable(_plan(eligible=3))


# ── stance → tier ──────────────────────────────────────────────────────────
def test_competitors_are_never_a_recipient():
    assert "sells_alternative" in outreach.EXCLUDED_STANCES
    assert "sells_alternative" not in outreach.STANCE_TIER


def test_stance_maps_to_the_tier_that_matches_what_they_said():
    assert outreach.STANCE_TIER["requests_solution"] == "deployed"
    assert outreach.STANCE_TIER["reports_pain"] == "instructions"
    assert outreach.STANCE_TIER["offers_workaround"] == "roadmap"


# ── the message ────────────────────────────────────────────────────────────
def test_the_message_quotes_them_back_with_a_citation_and_an_unsubscribe():
    body = outreach.compose(
        {"quote": "I lose a day a week chasing unmatched invoices",
         "tier": "instructions", "evidence_id": 99,
         "unsubscribe_url": "https://x.test/u/7"},
        {"promise": "cut that to an hour"},
        "https://pay.test/p/1")
    assert "chasing unmatched invoices" in body
    assert "evidence #99" in body
    assert "https://x.test/u/7" in body
    assert "https://pay.test/p/1" in body


def test_every_recipient_gets_a_distinct_unsubscribe_url():
    a = outreach.compose({"quote": "q", "tier": "roadmap", "evidence_id": 1,
                          "unsubscribe_url": "https://x.test/u/1"}, {}, "u")
    b = outreach.compose({"quote": "q", "tier": "roadmap", "evidence_id": 1,
                          "unsubscribe_url": "https://x.test/u/2"}, {}, "u")
    assert a != b


@pytest.mark.asyncio
async def test_planning_without_an_unsubscribe_base_is_refused():
    """No unsubscribe path means no lawful send, before any row is written."""
    with pytest.raises(ValueError, match="unsubscribe"):
        await outreach.plan_launch(13, "")
