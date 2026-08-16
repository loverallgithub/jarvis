"""Claims that no artifact cites must still be checkable.

WHAT WENT WRONG, 2026-08-09
───────────────────────────
`factual()` walks `artifact_claims`, so it can only check claims a packaged
artifact already cites. Solution research produces claims BEFORE anything cites
them, and `market.copy` draws only on `supported IS TRUE`.

So: eight solution claims were extracted, `forge reverify` dutifully re-checked
the same fourteen artifact claims as before, and the eight stayed NULL. The
research ran, 96 usable evidence rows landed, and NOTHING DOWNSTREAM COULD SEE
ANY OF IT.

A claim nobody has checked is not usable as evidence. A claim nobody CAN check
is worse — it sits in the table looking like one.
"""
from __future__ import annotations

import pytest

from jarvis.forge import verify as vf


@pytest.fixture
def stub(monkeypatch):
    rows = [{"id": 58, "text": "Cardholders have 120 days to raise a chargeback.",
             "url": "https://gov.example/a", "body": "Cardholders have 120 days."},
            {"id": 59, "text": "Every vendor publishes a public price.",
             "url": "https://gov.example/b", "body": "Pricing is on request."}]
    updates: list = []

    async def fetch(q, *a):
        return rows

    async def execute(q, *a):
        updates.append(a)
        return "UPDATE 1"

    async def params():
        return {}

    monkeypatch.setattr(vf.db, "fetch", fetch)
    monkeypatch.setattr(vf.db, "execute", execute)
    from jarvis.research import evidence as ev
    monkeypatch.setattr(ev, "params", params)
    return updates


@pytest.mark.asyncio
async def test_unverified_claims_get_a_verdict(stub, monkeypatch):
    async def llm(prompt, **kw):
        ok = "120 days" in prompt
        return '{"supported": %s, "why": "checked"}' % ("true" if ok else "false")
    monkeypatch.setattr(vf, "_llm", llm)

    out = await vf.verify_claims(13)
    assert out["checked"] == 2
    assert out["supported"] == 1 and out["unsupported"] == 1
    assert len(stub) == 2, "verdicts were not persisted"


@pytest.mark.asyncio
async def test_an_unusable_answer_is_NEVER_treated_as_supported(stub, monkeypatch):
    """The rule the whole verifier rests on: unverifiable is not verified."""
    async def llm(prompt, **kw):
        return None
    monkeypatch.setattr(vf, "_llm", llm)

    out = await vf.verify_claims(13)
    assert out["supported"] == 0
    assert all(d["supported"] is False for d in out["detail"])


@pytest.mark.asyncio
async def test_a_malformed_reply_is_not_supported(stub, monkeypatch):
    async def llm(prompt, **kw):
        return "I think probably yes, on balance."
    monkeypatch.setattr(vf, "_llm", llm)
    assert (await vf.verify_claims(13))["supported"] == 0


@pytest.mark.asyncio
async def test_it_only_walks_unverified_claims_by_default(monkeypatch):
    seen = {}

    async def fetch(q, *a):
        seen["q"] = q
        return []

    monkeypatch.setattr(vf.db, "fetch", fetch)
    await vf.verify_claims(13)
    assert "supported IS NULL" in seen["q"]

    await vf.verify_claims(13, only_unverified=False)
    assert "supported IS NULL" not in seen["q"]


@pytest.mark.asyncio
async def test_nothing_to_check_is_not_an_error(monkeypatch):
    async def fetch(q, *a):
        return []
    monkeypatch.setattr(vf.db, "fetch", fetch)
    out = await vf.verify_claims(13)
    assert out["checked"] == 0 and out["detail"] == []


@pytest.mark.asyncio
async def test_the_excerpt_selection_is_shared_with_the_artifact_path(stub,
                                                                     monkeypatch):
    """Two verification paths must not diverge in how they choose evidence, or
    the same claim gets different verdicts depending on who asks."""
    used = {}

    async def llm(prompt, **kw):
        used["prompt"] = prompt
        return '{"supported": true, "why": "ok"}'

    monkeypatch.setattr(vf, "_llm", llm)
    await vf.verify_claims(13)
    assert "SOURCE TEXT (excerpt):" in used["prompt"]
    assert "Absence of contradiction is NOT support" in used["prompt"]
