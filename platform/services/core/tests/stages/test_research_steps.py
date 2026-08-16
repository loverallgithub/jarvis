"""Stage tests for Phase B — research and grounding.

The `@step` decorator refuses to register a step whose declared test file does
not exist, so this file existing is a precondition for the steps existing.
"""
from __future__ import annotations

import pytest

from jarvis import db
from jarvis.research import steps as rsteps
from jarvis.runtime import registry


async def test_the_research_steps_are_registered(clean_db):
    rsteps.register()
    ids = set(registry.all_steps())
    for expected in ("research.capture", "research.gap_analysis",
                     "research.willingness_to_pay", "research.feasibility",
                     "research.synthesise"):
        assert expected in ids, f"{expected} is not registered"
    assert registry.validate_registry() == []


async def test_a_claim_cannot_be_written_without_evidence(clean_db):
    """🔴 C4, at the database level. This is the constraint the whole phase
    rests on: the only way to get an evidence_id is to have fetched something."""
    need = await db.fetchval("INSERT INTO needs (title) VALUES ('t') RETURNING id")
    with pytest.raises(Exception):
        await db.execute(
            "INSERT INTO claims (need_id, text, evidence_id) VALUES ($1,'x',NULL)", need)


async def test_a_claim_must_belong_to_something(clean_db):
    """Phase B made deliverable_id nullable so claims can attach to a NEED.
    An orphan claim attached to neither is a quieter version of the very
    problem this table exists to prevent."""
    ev = await db.fetchval(
        "INSERT INTO evidence (sha256, url) VALUES ('h','https://x.test') RETURNING id")
    with pytest.raises(Exception):
        await db.execute(
            "INSERT INTO claims (text, evidence_id) VALUES ('orphan', $1)", ev)
