"""Clamped watermarks.

The defect: Pimlico stored ``hermes:n8n:last_seen_execution = 1757`` — a
high-water mark carried over from a *different* n8n instance — against a local
maximum execution id of 34. ``id > last_seen`` was unsatisfiable forever, so
the failure watcher reported "no failures" every single day while WF05 was
erroring. It was blind for weeks and said nothing.

The fix is arithmetic: a cursor may never exceed the highest value actually
observed from the backend it points at.
"""
from __future__ import annotations

import pytest

from jarvis import db
from jarvis.runtime import watermark


async def test_a_wedged_cursor_self_heals(clean_db):
    """The exact incident, reproduced and then fixed by the clamp."""
    await watermark.advance("n8n:last_seen", 1757, observed_max=1757)
    assert await watermark.read("n8n:last_seen", observed_max=1757) == 1757

    # The backend is swapped for one whose highest id is 34.
    effective = await watermark.read("n8n:last_seen", observed_max=34)

    assert effective == 34, "the cursor must clamp, or the watcher is blind forever"
    row = await db.fetchrow("SELECT * FROM watermarks WHERE name = 'n8n:last_seen'")
    assert row["value"] == 34
    assert row["clamped_at"] is not None, "a clamp must be RECORDED, not silent"


async def test_an_empty_response_does_not_clamp(clean_db):
    """A backend returning nothing is not evidence that it reset.

    Clamping to 0 on an empty page would re-deliver the entire history on the
    next poll — turning a blind watcher into a duplicate-alert storm.
    """
    await watermark.advance("q", 500, observed_max=500)
    assert await watermark.read("q", observed_max=0) == 500
    row = await db.fetchrow("SELECT clamped_at FROM watermarks WHERE name = 'q'")
    assert row["clamped_at"] is None


async def test_a_cursor_never_moves_backwards(clean_db):
    await watermark.advance("q", 100, observed_max=100)
    assert await watermark.advance("q", 50, observed_max=100) == 100


async def test_a_cursor_cannot_advance_past_what_was_observed(clean_db):
    """Guards against the original bug being reintroduced at write time."""
    assert await watermark.advance("q", 9999, observed_max=34) == 34


async def test_an_unknown_cursor_reads_as_zero(clean_db):
    assert await watermark.read("never-seen", observed_max=10) == 0


async def test_normal_forward_progress(clean_db):
    assert await watermark.advance("q", 10, observed_max=10) == 10
    assert await watermark.read("q", observed_max=20) == 10
    assert await watermark.advance("q", 20, observed_max=20) == 20
