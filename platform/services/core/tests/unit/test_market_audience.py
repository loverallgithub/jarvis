"""F1 must derive the audience from the PRODUCT, never from `needs.audience`.

WHAT WENT WRONG, 2026-08-09, need 13
────────────────────────────────────
`needs.audience` held the literal string **"5 distinct voices"** — a COUNT,
written into a field meant to hold a segment, by phase A qualification. F1 read
it, found it useless, and invented a plausible-sounding replacement:

    positioning:  "Finance teams at companies with 50-500 employees"
    artifacts:    "1. The owner-operator (1-10 people, no finance hire)"
                  "2. The office manager / bookkeeper at a 10-50 person business"

The five real voices were individual App Store reviewers, not enterprise finance
teams. Had `market.copy` run on that positioning, all fifteen blocks would have
been written for the wrong buyer, at a price ladder anchored for a different one
— and nothing downstream would have caught it, because copy is checked for
CITATION, not for whether it addresses the right person.

The fix has two halves and both are tested here: recognise a placeholder rather
than trusting the column, and read the audience out of the product's own
`Who This Is For` section, which the forge generates from research and which
cannot drift from the deliverable because it IS the deliverable.
"""
from __future__ import annotations

import pytest

from jarvis.market.copy import usable_audience


# ── the placeholder guard ──────────────────────────────────────────────────
@pytest.mark.parametrize("value", [
    "5 distinct voices",      # the exact production value
    "12 distinct voices",
    "3 voices",
    "7 signals",
    "4 mentions",
    "9 sources",
    "N/A", "n/a", "TBD", "unknown", "none",
    "", "   ", None,
    "SMBs",                   # too short to be a segment
])
def test_placeholders_and_counts_are_rejected(value):
    assert usable_audience(value) is False


@pytest.mark.parametrize("value", [
    "The owner-operator of a 1-10 person business with no finance hire",
    "Office managers and bookkeepers at 10-50 person companies",
    "Finance leads at 50-250 person companies running a real ERP",
])
def test_a_real_segment_is_accepted(value):
    assert usable_audience(value) is True


def test_a_count_embedded_in_a_real_sentence_is_still_accepted():
    """The guard matches a value that IS a count, not any value containing one —
    otherwise a legitimate segment naming a company size gets thrown away."""
    assert usable_audience(
        "Owner-operators at businesses of 5 distinct sizes, 1-50 staff") is True


# ── reading the product's own section ──────────────────────────────────────
@pytest.mark.asyncio
async def test_the_audience_is_read_from_the_artifact(tmp_path, monkeypatch):
    from jarvis.market import copy as m

    art = tmp_path / "need-13-roadmap.md"
    art.write_text(
        "# Title\n\n## The Outcome\n\nSomething.\n\n"
        "## Who This Is For\n\n"
        "**1. The owner-operator (1-10 people, no finance hire).** You signed up.\n\n"
        "## Milestones & Critical Path\n\nOther content.\n")

    async def fetch(q, *a):
        return [{"tier": "roadmap", "storage_uri": f"file://{art}"}]
    monkeypatch.setattr(m.db, "fetch", fetch)

    out = await m.product_audience(13)
    assert "owner-operator (1-10 people" in out
    # and it stops at the next heading rather than swallowing the document
    assert "Milestones" not in out and "The Outcome" not in out


@pytest.mark.asyncio
async def test_a_missing_section_returns_empty_rather_than_guessing(tmp_path, monkeypatch):
    from jarvis.market import copy as m

    art = tmp_path / "a.md"
    art.write_text("# Title\n\n## The Outcome\n\nNo audience section here.\n")

    async def fetch(q, *a):
        return [{"tier": "roadmap", "storage_uri": f"file://{art}"}]
    monkeypatch.setattr(m.db, "fetch", fetch)

    assert await m.product_audience(13) == ""


@pytest.mark.asyncio
async def test_a_missing_file_does_not_crash(monkeypatch):
    from jarvis.market import copy as m

    async def fetch(q, *a):
        return [{"tier": "roadmap", "storage_uri": "file:///nope/missing.md"}]
    monkeypatch.setattr(m.db, "fetch", fetch)

    assert await m.product_audience(13) == ""


@pytest.mark.asyncio
async def test_positioning_REFUSES_when_no_audience_can_be_derived(monkeypatch):
    """Better to stop than to invent a buyer. Inventing one is exactly what
    produced '50-500 employees' for a product written for 1-10 person firms."""
    from jarvis.market import copy as m

    async def fetchrow(q, *a):
        return {"id": 13, "title": "t", "pain_statement": "p",
                "audience": "5 distinct voices"}

    async def _pack(need_id, limit=14):
        return [{"id": 1, "text": "a claim", "url": "https://e.test"}]

    async def _voices(need_id, limit=8):
        return []

    async def _prod_aud(need_id):
        return ""

    monkeypatch.setattr(m.db, "fetchrow", fetchrow)
    monkeypatch.setattr(m, "_evidence_pack", _pack)
    monkeypatch.setattr(m, "_voice_quotes", _voices)
    monkeypatch.setattr(m, "product_audience", _prod_aud)

    with pytest.raises(ValueError, match="cannot derive an audience"):
        await m.build_positioning(13)
