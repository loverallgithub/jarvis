"""Stage tests for Phase C/D/E — the forge.

The `@step` decorator refuses to register a step whose declared test file does
not exist, so this file existing is a precondition for the steps existing.
"""
from __future__ import annotations

import pytest

from jarvis import db
from jarvis.forge import steps as fsteps
from jarvis.runtime import registry


async def test_the_forge_steps_are_registered(clean_db):
    fsteps.register()
    ids = set(registry.all_steps())
    for expected in ("forge.plan", "forge.generate", "forge.package",
                     "forge.verify", "forge.acceptance_tests"):
        assert expected in ids, f"{expected} is not registered"
    assert registry.validate_registry() == []


def test_each_tier_is_a_superset_of_the_one_below():
    """Instructions = Roadmap + build manual. Deployed = Instructions + the
    built thing. A buyer of the higher tier receives everything below it."""
    from jarvis.forge import plan
    r = {s.key for s in plan.sections_for("roadmap")}
    i = {s.key for s in plan.sections_for("instructions")}
    d = {s.key for s in plan.sections_for("deployed")}
    assert r < i < d, "each tier must strictly contain the one below it"


def test_the_size_cap_truncates_the_PLAN_not_just_the_output():
    """🔴 Pimlico capped generated output while keeping the full plan, so
    `verify` then failed on "fewer sections than planned" — the run was
    guaranteed to fail AFTER paying for every section it did generate."""
    from jarvis.forge import plan
    full = plan.sections_for("deployed")
    capped = plan.sections_for("deployed", max_sections=3)
    assert len(capped) == 3
    assert len(full) > 3
    # The plan the verifier will check against IS the capped plan.
    assert [s.key for s in capped] == [s.key for s in full[:3]]


def test_llm_text_extraction_handles_a_leading_non_text_block():
    """🔴 `claude-opus-5` returns 200 with a content array whose FIRST block is
    not text. `content[0]["text"]` raised KeyError, `_llm` swallowed it as None,
    and the forge produced zero sections across three tiers in 691 seconds
    while paying for every call."""
    from jarvis.forge.build import _text_of

    assert _text_of({"content": [{"type": "text", "text": "hello"}]}) == "hello"
    # thinking block first — the shape that broke it
    assert _text_of({"content": [
        {"type": "thinking", "thinking": "hmm"},
        {"type": "text", "text": "the answer"}]}) == "the answer"
    # several text blocks are concatenated, not truncated to the first
    assert _text_of({"content": [
        {"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}) == "a\nb"
    # genuinely no text is None, NOT an empty string that reads as success
    assert _text_of({"content": [{"type": "thinking", "thinking": "x"}]}) is None
    assert _text_of({"content": []}) is None
    assert _text_of({}) is None


def test_placeholder_detection_does_not_fire_on_legitimate_prose():
    """🔴 Two good artifacts were withheld by naive substring matching.

    Both strings below are from the real generated documents and both are
    correct, finished prose. A verifier that withholds good work is as damaging
    as one that passes bad work — and it is the failure people 'fix' by turning
    the gate off.
    """
    from jarvis.forge.build import find_placeholders

    assert find_placeholders(
        'AP vendors publish no pricing at all, or "custom quote" placeholders, '
        'which makes cost comparison impossible.') == []
    assert find_placeholders(
        'Say: "...disputing the charges as services not rendered — I cannot '
        'access the account and the merchant has not responded."') == []
    assert find_placeholders("Set the XXXX-XXXX code aside for now.") != []


def test_placeholder_detection_still_catches_real_unfinished_work():
    from jarvis.forge.build import find_placeholders

    for bad in ("Lorem ipsum dolor sit amet",
                "Pricing: TBD",
                "TODO: write this section",
                "Enter [insert API key] here",
                "cost is PLACEHOLDER per seat",
                "This feature is coming soon.",
                "As an AI language model, I cannot help with that.",
                "I'm sorry, I can't produce that document.",
                "[Automation failed: Page.goto timeout]"):
        assert find_placeholders(bad), f"failed to catch: {bad!r}"

    # 🔴 `[your ...]` MOVED SIDES on 2026-08-09, by operator decision.
    # This case used to read "Enter [your API key] here" and was expected to
    # FAIL the document. It no longer does, and that is the point: the buyer
    # entering their own API key is the template working, not the author leaving
    # a gap. Artifacts #7 and #8 were withheld from sale solely over
    # `[your billing descriptor]` and `[your account email]`.
    for ok in ("Enter [your API key] here",
               "Subject: cancellation — [your billing descriptor]",
               "Reply-to: [your account email]"):
        assert not find_placeholders(ok), f"wrongly flagged a buyer field: {ok!r}"


async def test_a_claim_can_be_cited_by_all_three_tiers(clean_db):
    """🔴 `claims.deliverable_id` is single-valued, so packaging three tiers made
    each one STEAL the citations from the last.

    Observed on need 13: roadmap and instructions ended with 0 claims and were
    marked factually OK *vacuously*, while deployed held all 14. Two artifacts
    were declared offerable because their citations had been taken away, not
    because they were checked. The tiers are supersets, so the same claim is
    legitimately cited by all three — that is many-to-many.
    """
    need = await db.fetchval("INSERT INTO needs (title) VALUES ('t') RETURNING id")
    evid = await db.fetchval(
        "INSERT INTO evidence (need_id, sha256, url, body, substantive, live_at_capture) "
        "VALUES ($1,'h','https://x.test','some real supporting text',TRUE,TRUE) RETURNING id",
        need)
    claim = await db.fetchval(
        "INSERT INTO claims (need_id, text, evidence_id, kind) "
        "VALUES ($1,'a cited fact',$2,'gap') RETURNING id", need, evid)

    arts = []
    for tier in ("roadmap", "instructions", "deployed"):
        aid = await db.fetchval(
            "INSERT INTO artifacts (need_id, tier, kind, sha256, bytes, storage_uri) "
            "VALUES ($1,$2,'markdown','s',1,'file:///tmp/x') RETURNING id", need, tier)
        await db.execute(
            "INSERT INTO artifact_claims (artifact_id, claim_id) VALUES ($1,$2)",
            aid, claim)
        arts.append(aid)

    # Every tier still holds the citation — none of them stole it.
    for aid in arts:
        n = await db.fetchval(
            "SELECT count(*) FROM artifact_claims WHERE artifact_id=$1", aid)
        assert n == 1, "a tier lost its citation to a later package"


async def test_an_artifact_citing_nothing_is_unverified_not_verified(clean_db):
    """An artifact with no claims cannot be fact-checked, so it must NOT pass.
    Treating 'nothing to check' as 'checked and fine' is how the stolen-citation
    bug produced two false OFFERABLE verdicts."""
    from jarvis.forge import verify as vf

    need = await db.fetchval("INSERT INTO needs (title) VALUES ('t') RETURNING id")
    aid = await db.fetchval(
        "INSERT INTO artifacts (need_id, tier, kind, sha256, bytes, storage_uri) "
        "VALUES ($1,'roadmap','markdown','s',1,'file:///tmp/none') RETURNING id", need)

    res = vf.VerifyResult(artifact_id=int(aid))
    res = await vf.factual(int(aid), res)
    assert res.factual_ok is False
