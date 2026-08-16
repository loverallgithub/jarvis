"""One malformed page must not take out a whole capture run.

WHAT HAPPENED, 2026-08-09
─────────────────────────
The first solution-research run produced six well-formed queries — FTC dispute
procedure, NACHA chargeback rules, Regulation Z billing disputes — and stored
NOTHING. A page in the first query's eight results contained a NUL byte, the
INSERT raised `invalid byte sequence for encoding "UTF8": 0x00`, the exception
propagated, and the remaining five queries never ran.

Two separate defects, fixed separately because they fail differently:

  _pg_safe            Postgres text cannot hold a NUL. Strip it (and the other
                      C0 controls) before storage. Fixes THAT byte.
  per-page isolation  Capture walks the open web, so the next malformed thing
                      is a matter of when. Fixes the SHAPE of the failure.
"""
from __future__ import annotations

import pytest

from jarvis.research import evidence as ev


# ── the byte ───────────────────────────────────────────────────────────────
def test_a_nul_byte_is_stripped():
    assert "\x00" not in ev._pg_safe("before\x00after")
    assert ev._pg_safe("before\x00after") == "beforeafter"


def test_other_c0_controls_go_too():
    assert ev._pg_safe("a\x01b\x07c\x1fd") == "abcd"


def test_tab_newline_and_carriage_return_SURVIVE():
    """They appear in real extracted text; removing them would corrupt prose."""
    assert ev._pg_safe("a\tb\nc\rd") == "a\tb\nc\rd"


def test_empty_and_none_are_safe():
    assert ev._pg_safe("") == ""
    assert ev._pg_safe(None) == ""          # type: ignore[arg-type]


def test_ordinary_text_is_untouched():
    s = "Cardholders have 120 days — under §75 — to raise a claim."
    assert ev._pg_safe(s) == s


# ── the shape of the failure ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_one_failing_page_does_not_abort_the_batch(monkeypatch):
    """The exact production failure: page 2 explodes, pages 3-5 must still be
    captured and the run must return what it got."""
    async def search(self, query, limit=10):
        return [{"url": f"https://e.test/{i}"} for i in range(5)]

    async def fetch(url, timeout=None):
        if url.endswith("/1"):
            raise ValueError('invalid byte sequence for encoding "UTF8": 0x00')
        return object()

    stored: list[str] = []

    async def record(cap, *, need_id, kind="page", run_id=None):
        stored.append(kind)
        return len(stored)

    monkeypatch.setattr(ev.DuckDuckGoSearch, "search", search)
    monkeypatch.setattr(ev, "fetch", fetch)
    monkeypatch.setattr(ev, "record", record)

    ids = await ev.capture_search(13, "a query", limit=5)
    assert len(ids) == 4, "a single bad page cost the batch"


@pytest.mark.asyncio
async def test_a_batch_where_everything_fails_returns_empty_not_an_exception(
        monkeypatch):
    """The caller decides what an empty capture means; it must not have to
    catch."""
    async def search(self, query, limit=10):
        return [{"url": "https://e.test/x"}]

    async def fetch(url, timeout=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(ev.DuckDuckGoSearch, "search", search)
    monkeypatch.setattr(ev, "fetch", fetch)
    assert await ev.capture_search(13, "q") == []
