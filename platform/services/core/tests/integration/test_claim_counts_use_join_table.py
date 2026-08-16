"""`jpd forge artifacts` must count claims via `artifact_claims`, not the dead
`claims.deliverable_id` column.

WHAT WENT WRONG
───────────────
Migration 014 replaced the single-valued `deliverable_id` with the
`artifact_claims` join table, because the three tiers are supersets and
legitimately cite the SAME claim. Before that, packaging each tier STOLE the
citations from the previous one, and two artifacts were marked OFFERABLE with
`claims_checked = 0` — passed because they had nothing left to check.

The join table fixed packaging and verification. It did NOT fix the CLI summary,
which kept reading `deliverable_id` — a column nothing writes any more. It
therefore reported the truth only for claims old enough to still carry the
legacy value.

Demonstrated 2026-08-09 on need 13: one extra unsupported claim, linked the way
packaging links today, produced "2 unsupported" against a true count of 3. The
miss is silent and fails toward a FALSE ALL-CLEAR.

These tests assert both directions: a claim linked only via the join table is
counted, and one claim cited by three tiers is counted ONCE.
"""
from __future__ import annotations

import pytest

from jarvis import db

pytestmark = pytest.mark.integration


async def _count_uncited(need_id: int) -> int:
    return int(await db.fetchval(
        "SELECT count(DISTINCT c.id) FROM artifact_claims ac "
        "  JOIN claims c ON c.id = ac.claim_id "
        "  JOIN artifacts a ON a.id = ac.artifact_id "
        " WHERE a.need_id = $1 AND c.evidence_id IS NULL", need_id) or 0)


async def _count_unsupported(need_id: int) -> int:
    return int(await db.fetchval(
        "SELECT count(DISTINCT c.id) FROM artifact_claims ac "
        "  JOIN claims c ON c.id = ac.claim_id "
        "  JOIN artifacts a ON a.id = ac.artifact_id "
        " WHERE a.need_id = $1 AND c.supported = FALSE", need_id) or 0)


async def _fixture(clean_db) -> tuple[int, list[int], int]:
    """One need, three tier artifacts, one evidence row."""
    need_id = int(await db.fetchval(
        "INSERT INTO needs (title, status) VALUES ('n', 'promoted') RETURNING id"))
    ev_id = int(await db.fetchval(
        "INSERT INTO evidence (need_id, url, sha256, http_status, live_at_capture, "
        "                      substantive, body, bytes, kind) "
        "VALUES ($1,'https://e.com/a',$2,200,TRUE,TRUE,'body',4,'page') RETURNING id",
        need_id, "a" * 64))
    arts = []
    for tier in ("roadmap", "instructions", "deployed"):
        arts.append(int(await db.fetchval(
            "INSERT INTO artifacts (need_id, tier, kind, sha256, bytes, storage_uri, "
            "                       title, sections, words) "
            "VALUES ($1,$2,'md',$3,10,'file:///tmp/x.md','t',1,10) RETURNING id",
            need_id, tier, f"{tier}sha")))
    return need_id, arts, ev_id


@pytest.mark.asyncio
async def test_a_claim_linked_only_via_the_join_table_is_counted(clean_db):
    """The exact production bug: packaging writes artifact_claims and leaves
    deliverable_id NULL, so a deliverable_id-based count misses it entirely."""
    need_id, arts, ev_id = await _fixture(clean_db)
    claim_id = int(await db.fetchval(
        "INSERT INTO claims (need_id, text, evidence_id, supported) "
        "VALUES ($1,'unsupported claim',$2,FALSE) RETURNING id", need_id, ev_id))
    await db.execute("INSERT INTO artifact_claims (artifact_id, claim_id) VALUES ($1,$2)",
                     arts[0], claim_id)

    assert await _count_unsupported(need_id) == 1

    # and the dead column sees nothing, which is what made this silent
    legacy = int(await db.fetchval(
        "SELECT count(*) FROM claims WHERE deliverable_id IN "
        "(SELECT id FROM artifacts WHERE need_id=$1) AND supported = FALSE",
        need_id) or 0)
    assert legacy == 0, "deliverable_id is dead — a count based on it must not be trusted"


@pytest.mark.asyncio
async def test_one_claim_cited_by_three_tiers_counts_ONCE(clean_db):
    """Three artifacts share a claim set. Counting rows instead of DISTINCT
    claims reported 4 real failures as '12 unsupported' and made the remaining
    work look three times larger than it was."""
    need_id, arts, ev_id = await _fixture(clean_db)
    claim_id = int(await db.fetchval(
        "INSERT INTO claims (need_id, text, evidence_id, supported) "
        "VALUES ($1,'shared claim',$2,FALSE) RETURNING id", need_id, ev_id))
    for a in arts:
        await db.execute(
            "INSERT INTO artifact_claims (artifact_id, claim_id) VALUES ($1,$2)",
            a, claim_id)

    assert await db.fetchval(
        "SELECT count(*) FROM artifact_claims WHERE claim_id=$1", claim_id) == 3
    assert await _count_unsupported(need_id) == 1


@pytest.mark.asyncio
async def test_uncited_claims_is_a_TAUTOLOGY_not_a_measurement(clean_db):
    """🔴 `claims.evidence_id` is NOT NULL, so `WHERE evidence_id IS NULL` can
    never match and `uncited_claims` is permanently 0.

    That makes `forge.verify`'s acceptance predicate — `uncited_claims == 0` —
    a condition that CANNOT FAIL, and the second conjunct of
    `factual_ok = (not unsupported) and uncited_claims == 0` dead weight.

    The invariant itself is good and worth keeping: a claim row must cite
    evidence, and the schema enforces it. The problem is REPORTING it as though
    it were verified. "0 uncited claims" has been quoted as an achievement in
    every checkpoint; it is a column constraint.

    What the phrase SOUNDS like it means — every factual assertion in the
    finished document carries a [claim N] citation — is not measured anywhere.
    A sentence the generator wrote without citing anything produces no claim
    row, so it cannot be counted as uncited, and `structural()` checks sections,
    placeholders and length but never citation coverage. THAT is the real gap.

    This test pins the constraint so the tautology is visible in the suite
    rather than hidden behind a metric that always reads clean.
    """
    need_id, arts, _ = await _fixture(clean_db)
    with pytest.raises(Exception) as exc:
        await db.execute(
            "INSERT INTO claims (need_id, text, evidence_id, supported) "
            "VALUES ($1,'no evidence',NULL,TRUE)", need_id)
    assert "evidence_id" in str(exc.value)
    # therefore, and always:
    assert await _count_uncited(need_id) == 0


@pytest.mark.asyncio
async def test_a_clean_need_reports_zero_without_the_query_being_vacuous(clean_db):
    """Zero must mean 'checked and clean', not 'found nothing to look at'."""
    need_id, arts, ev_id = await _fixture(clean_db)
    claim_id = int(await db.fetchval(
        "INSERT INTO claims (need_id, text, evidence_id, supported) "
        "VALUES ($1,'good claim',$2,TRUE) RETURNING id", need_id, ev_id))
    await db.execute("INSERT INTO artifact_claims (artifact_id, claim_id) VALUES ($1,$2)",
                     arts[0], claim_id)
    assert await _count_unsupported(need_id) == 0
    assert await _count_uncited(need_id) == 0
    assert await db.fetchval(
        "SELECT count(*) FROM artifact_claims WHERE artifact_id=$1", arts[0]) == 1


@pytest.mark.asyncio
async def test_claims_of_another_need_do_not_leak_into_the_count(clean_db):
    need_a, arts_a, ev_a = await _fixture(clean_db)
    need_b, arts_b, ev_b = await _fixture(clean_db)
    cb = int(await db.fetchval(
        "INSERT INTO claims (need_id, text, evidence_id, supported) "
        "VALUES ($1,'other need',$2,FALSE) RETURNING id", need_b, ev_b))
    await db.execute("INSERT INTO artifact_claims (artifact_id, claim_id) VALUES ($1,$2)",
                     arts_b[0], cb)
    assert await _count_unsupported(need_a) == 0
    assert await _count_unsupported(need_b) == 1
