"""`jpd market remeasure` — re-score stored copy when the MEASUREMENT changed.

Regenerating correct text to satisfy a changed metric pays an LLM for work
that was already right (the four-word "thin" section lesson). Remeasure runs
the current `citation_coverage` — carve-outs included — over the stored
bodies, for free, and updates what `market.pages` will read.
"""
from __future__ import annotations

import pytest

from jarvis import db
from jarvis.market import copy as mcopy
from jarvis.runtime import scheduler


async def test_remeasure_rescores_stored_blocks_for_free(clean_db):
    need = await db.fetchval(
        "INSERT INTO needs (title) VALUES ('__rm__') RETURNING id")
    sol = await db.fetchval(
        "INSERT INTO solutions (need_id, title) VALUES ($1,'__rm__') RETURNING id",
        need)
    await db.execute(
        "INSERT INTO offers (solution_id, tier, price_minor, live) "
        "VALUES ($1,'instructions',4000,TRUE)", sol)
    # One block, three lines: a cited world-claim, an offer price that matches
    # the ladder, and an audience sentence. Under the pre-carve-out measurement
    # the last two were uncited checkables — stored here as the 50% they scored.
    body = ("The escalation steps most vendors typically use [claim 1].\n\n"
            "You pay €40 once — a one-time purchase with no subscription.\n\n"
            "This is for owner-operators and small business owners "
            "(1–15 employees, no dedicated finance staff).")
    await db.execute(
        "INSERT INTO copy_blocks (need_id, tier, block, body, citation_pct, "
        "citation_checkable) VALUES ($1,'instructions','faq',$2,50.0,2)",
        need, body)

    out = await mcopy.remeasure(need)
    assert out["blocks"] == 1 and out["changed"] == 1
    assert out["below_floor"] == []
    pct = await db.fetchval(
        "SELECT citation_pct FROM copy_blocks WHERE need_id=$1", need)
    assert float(pct) == 100.0


async def test_remeasure_still_fails_what_deserves_to_fail(clean_db):
    """The carve-out is not a rubber stamp: an uncited world-claim and a WRONG
    price both stay below the floor."""
    need = await db.fetchval(
        "INSERT INTO needs (title) VALUES ('__rm2__') RETURNING id")
    sol = await db.fetchval(
        "INSERT INTO solutions (need_id, title) VALUES ($1,'__rm2__') RETURNING id",
        need)
    await db.execute(
        "INSERT INTO offers (solution_id, tier, price_minor, live) "
        "VALUES ($1,'instructions',4000,TRUE)", sol)
    body = ("Most vendors resolve lockouts within 48 hours.\n\n"
            "You pay €45 once — a one-time purchase with no subscription.")
    await db.execute(
        "INSERT INTO copy_blocks (need_id, tier, block, body, citation_pct, "
        "citation_checkable) VALUES ($1,'instructions','faq',$2,100.0,0)",
        need, body)

    out = await mcopy.remeasure(need)
    assert len(out["below_floor"]) == 1
    assert float(out["below_floor"][0]["citation_pct"]) == 0.0


async def test_the_driverless_manifest_job_is_disabled(clean_db):
    """018: a registry row with no driver is a promise nobody is keeping.
    Its ground is covered by commerce.artifact_sweep; if it ever comes back,
    it comes back WITH a DISPATCH entry."""
    assert await db.fetchval(
        "SELECT enabled FROM job_registry "
        " WHERE job_name='integrity.manifest_check'") is False
    assert "integrity.manifest_check" not in scheduler.DISPATCH
