"""Targeted regeneration: fix one thing without paying to rebuild everything.

WHY BOTH TOOLS EXIST
────────────────────
`jpd forge run` regenerates all three tiers at $6-9 and OVERWRITES every draft.
Measured on need 13: the `estimate` section came back at FOUR WORDS against a
120-word minimum, and that alone withheld the roadmap tier from sale. Paying six
dollars and replacing two good artifacts to fix four words is a rebuild, not a
repair.

`market.copy` writes 15 blocks at ~$4. Seven of those already cleared the 90%
floor and eight did not, so re-running the step pays twice for seven that were
already right — and replaces them with fresh text that might not be.

THE SUBTLE BUG THESE TESTS GUARD
────────────────────────────────
`repair_section` must replace the section IN PLACE. Appending it would move the
section to the end of the document, and `structural()` checks that headings are
PRESENT, not that they are in order — so a reordered document passes
verification while reading as nonsense. That damage would be invisible.
"""
from __future__ import annotations

import pytest

from jarvis.forge import build


def _sections(keys):
    return [build.GeneratedSection(key=k, heading=k.title(), text=f"## {k.title()}\n\nbody",
                                   words=150, claim_ids=[1]) for k in keys]


@pytest.fixture
def draft(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "DRAFT_DIR", tmp_path)
    monkeypatch.setattr(build, "_draft_path",
                        lambda n, t: tmp_path / f"need-{n}-{t}.json")
    return tmp_path


@pytest.fixture
def patched(monkeypatch):
    async def need(q, *a):
        return {"id": 13, "title": "t", "pain_statement": "p", "audience": "a"}

    async def claims(need_id, limit=14):
        return [{"id": 1, "text": "c", "kind": "fact", "url": "u",
                 "title": "t", "sha": "abc", "snippet": "s"}]

    monkeypatch.setattr(build.db, "fetchrow", need)
    monkeypatch.setattr(build, "_claims_for", claims)


# ── ordering: the invisible-damage case ────────────────────────────────────
@pytest.mark.asyncio
async def test_a_repaired_section_keeps_its_position(draft, patched, monkeypatch):
    """The bug this guards: appending moves the section to the end, and
    structural() checks presence not order — so it would pass while the
    document reads as nonsense."""
    keys = ["outcome", "audience", "milestones", "stack", "estimate", "risks"]
    build.save_draft(13, "roadmap", _sections(keys))

    async def gen(need, section, claims, tier):
        return build.GeneratedSection(key=section.key, heading=section.heading,
                                      text="## Effort, Cost & Confidence\n\nnew body",
                                      words=200, claim_ids=[1])
    monkeypatch.setattr(build, "generate_section", gen)

    await build.repair_section(13, "roadmap", "estimate")
    after = [s.key for s in build.load_draft(13, "roadmap")]
    assert after == keys, "section order changed"


@pytest.mark.asyncio
async def test_only_the_named_section_changes(draft, patched, monkeypatch):
    keys = ["outcome", "audience", "milestones", "stack", "estimate", "risks"]
    build.save_draft(13, "roadmap", _sections(keys))
    before = {s.key: s.text for s in build.load_draft(13, "roadmap")}

    async def gen(need, section, claims, tier):
        return build.GeneratedSection(key=section.key, heading=section.heading,
                                      text="REPAIRED", words=200, claim_ids=[])
    monkeypatch.setattr(build, "generate_section", gen)

    await build.repair_section(13, "roadmap", "estimate")
    after = {s.key: s.text for s in build.load_draft(13, "roadmap")}
    assert after["estimate"] == "REPAIRED"
    for k in keys:
        if k != "estimate":
            assert after[k] == before[k], f"{k} was modified"


# ── failure modes leave the draft alone ────────────────────────────────────
@pytest.mark.asyncio
async def test_a_failed_regeneration_leaves_the_draft_UNCHANGED(draft, patched,
                                                                monkeypatch):
    """Paid-for work must survive a failed repair."""
    build.save_draft(13, "roadmap", _sections(["outcome", "estimate"]))
    before = build.load_draft(13, "roadmap")

    async def gen(need, section, claims, tier):
        return None
    monkeypatch.setattr(build, "generate_section", gen)

    with pytest.raises(RuntimeError, match="UNCHANGED"):
        await build.repair_section(13, "roadmap", "estimate")
    after = build.load_draft(13, "roadmap")
    assert [s.text for s in after] == [s.text for s in before]


@pytest.mark.asyncio
async def test_an_unknown_section_is_refused_with_the_valid_list(draft, patched):
    build.save_draft(13, "roadmap", _sections(["outcome"]))
    with pytest.raises(ValueError, match="unknown section"):
        await build.repair_section(13, "roadmap", "not_a_section")


@pytest.mark.asyncio
async def test_no_draft_tells_you_to_generate_first(draft, patched):
    with pytest.raises(ValueError, match="no draft on disk"):
        await build.repair_section(13, "roadmap", "estimate")


@pytest.mark.asyncio
async def test_the_word_count_is_reported_against_the_minimum(draft, patched,
                                                              monkeypatch):
    build.save_draft(13, "roadmap", _sections(["estimate"]))

    async def gen(need, section, claims, tier):
        return build.GeneratedSection(key=section.key, heading=section.heading,
                                      text="short", words=4, claim_ids=[])
    monkeypatch.setattr(build, "generate_section", gen)

    out = await build.repair_section(13, "roadmap", "estimate")
    assert out["words_after"] == 4
    assert out["meets_minimum"] is False
    assert out["min_words"] >= 100


# ── recopy targeting ───────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_below_floor_only_skips_the_blocks_that_already_pass(monkeypatch):
    """The whole point: 7 of 15 blocks were already fine. Re-running them pays
    twice and risks replacing good copy with worse."""
    from jarvis.market import copy as m

    async def fetchrow(q, *a):
        return {"pain_phrase": "p", "audience": "a", "promise": "pr", "proof": "pf"}

    async def pack(need_id, limit=14):
        return [{"id": 1, "text": "c", "url": "u"}]

    async def fetch(q, *a):
        return [{"tier": "roadmap", "block": "headline", "citation_pct": 100.0},
                {"tier": "roadmap", "block": "benefits", "citation_pct": 40.0}]

    built: list = []

    async def build_block(need_id, tier, block, claims, pos):
        built.append((tier, block))
        return {"tier": tier, "block": block, "body": "x [claim 1]",
                "citation_pct": 100.0, "citation_checkable": 1,
                "cited_claim_ids": [1], "examples": []}

    async def store(need_id, b, run_id=None):
        return None

    monkeypatch.setattr(m.db, "fetchrow", fetchrow)
    monkeypatch.setattr(m.db, "fetch", fetch)
    monkeypatch.setattr(m, "_evidence_pack", pack)
    monkeypatch.setattr(m, "build_block", build_block)
    monkeypatch.setattr(m, "store_block", store)

    await m.recopy(13, tier="roadmap", below_floor_only=True)
    assert ("roadmap", "headline") not in built, "regenerated a passing block"
    assert ("roadmap", "benefits") in built
    # the three blocks with no row yet count as below floor and are generated
    assert len(built) == 4
