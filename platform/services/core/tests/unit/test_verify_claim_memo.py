"""A claim is fact-checked ONCE per run, not once per artifact that cites it.

Claims belong to the NEED. All three tiers of need 13 cite the same 14 claims
(`artifact_claims`: 42 rows, 14 distinct, 3 artifacts). Verifying per artifact
therefore asked the LLM the same question three times, wrote all three answers
to the same `claims` row, and let the last one win.

Measured 2026-08-08, one run, identical inputs: the three passes returned 3, 3
and 2 unsupported over the same 14 claims, and claim 33 flipped from supported to
unsupported with nothing changed but the pass. `offerable` was partly a coin
flip, at 3x the LLM cost, and the flags written for artifacts 6 and 7 described
verdicts that artifact 8's pass then overwrote.

These tests pin the two properties that fixes it: each claim costs one call, and
two artifacts sharing a claim set get the SAME answer.
"""
from __future__ import annotations

import pytest

from jarvis.forge import verify as vf


class _Recorder:
    """Counts LLM calls and returns an answer that FLIPS every time.

    Alternating is the point: with a memo the flip can never be observed, and
    without one it shows up as two artifacts disagreeing about one claim — which
    is exactly the production symptom.
    """

    def __init__(self):
        self.calls = 0

    async def __call__(self, prompt, **kw):
        self.calls += 1
        supported = "true" if self.calls % 2 else "false"
        return '{"supported": %s, "why": "call %d"}' % (supported, self.calls)


@pytest.fixture
def rec(monkeypatch):
    r = _Recorder()
    monkeypatch.setattr(vf, "_llm", r)
    return r


# THREE claims, deliberately an ODD number. With two, a strictly alternating
# stub produces the identical true/false pattern on every pass, so the
# "without a memo the answers diverge" control passed vacuously — it could not
# have detected the bug it exists to demonstrate.
CLAIMS = [{"id": 1, "text": "claim one", "url": "https://e.com/a",
           "body": "a body long enough to be worth quoting"},
          {"id": 2, "text": "claim two", "url": "https://e.com/b",
           "body": "another body"},
          {"id": 3, "text": "claim three", "url": "https://e.com/c",
           "body": "a third body"}]


@pytest.fixture
def stub_db(monkeypatch):
    async def fetch(q, *a):
        return CLAIMS if "artifact_claims" in q and "SELECT c.id" in q else []

    async def fetchval(q, *a):
        return 0

    async def execute(q, *a):
        return "UPDATE 1"

    async def params():
        return {}

    monkeypatch.setattr(vf.db, "fetch", fetch)
    monkeypatch.setattr(vf.db, "fetchval", fetchval)
    monkeypatch.setattr(vf.db, "execute", execute)
    from jarvis.research import evidence as ev
    monkeypatch.setattr(ev, "params", params)


def _res():
    return vf.VerifyResult(artifact_id=1, file_present=True)


@pytest.mark.asyncio
async def test_two_artifacts_sharing_claims_cost_one_check_each(rec, stub_db):
    memo: dict = {}
    await vf.factual(1, _res(), memo)
    first = rec.calls
    await vf.factual(2, _res(), memo)
    assert first == len(CLAIMS)
    assert rec.calls == first, "the second artifact re-asked the LLM"


@pytest.mark.asyncio
async def test_two_artifacts_sharing_claims_get_the_SAME_verdicts(rec, stub_db):
    """The determinism property. The stub flips its answer on every call, so
    without the memo these two results necessarily disagree."""
    memo: dict = {}
    a = await vf.factual(1, _res(), memo)
    b = await vf.factual(2, _res(), memo)
    assert a.claims_supported == b.claims_supported
    assert ([u["claim_id"] for u in a.unsupported]
            == [u["claim_id"] for u in b.unsupported])
    assert a.factual_ok == b.factual_ok


@pytest.mark.asyncio
async def test_without_a_shared_memo_the_answers_diverge(rec, stub_db):
    """Proves the test above is not vacuous — the flipping stub really does
    produce disagreement when each artifact is verified independently."""
    a = await vf.factual(1, _res(), {})
    b = await vf.factual(2, _res(), {})
    assert a.claims_supported != b.claims_supported


@pytest.mark.asyncio
async def test_the_memo_is_populated_with_every_decided_claim(rec, stub_db):
    memo: dict = {}
    await vf.factual(1, _res(), memo)
    assert set(memo) == {1, 2, 3}
    for supported, why in memo.values():
        assert isinstance(supported, bool) and isinstance(why, str)


@pytest.mark.asyncio
async def test_omitting_the_memo_still_works_for_single_artifact_callers(
        rec, stub_db):
    res = await vf.factual(1, _res())
    assert res.claims_checked == len(CLAIMS)


@pytest.mark.asyncio
async def test_a_reused_verdict_is_still_counted_in_claims_checked(rec, stub_db):
    """Reuse must not make an artifact look UNVERIFIED — claims_checked is what
    distinguishes 'nothing to check' from 'checked and fine'."""
    memo: dict = {}
    await vf.factual(1, _res(), memo)
    second = await vf.factual(2, _res(), memo)
    assert second.claims_checked == len(CLAIMS)


@pytest.mark.asyncio
async def test_an_unsupported_reused_verdict_still_blocks_factual_ok(rec, stub_db):
    """The memo must not launder a failure into a pass on the second artifact."""
    memo = {1: (False, "seeded failure"), 2: (True, "fine"), 3: (True, "fine")}
    res = await vf.factual(2, _res(), memo)
    assert res.factual_ok is False
    assert any(u["claim_id"] == 1 for u in res.unsupported)
    assert rec.calls == 0, "a fully-memoised artifact should cost nothing"
