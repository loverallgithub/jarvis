"""A disabled gate is not a passed gate.

🔴 The defect these tests exist for, found 2026-08-08 while auditing for the
third instance of the "vacuous pass" shape (lessons 39 and 54):

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)   # all([]) is True

`add()` silently skipped any gate absent from `gate_thresholds WHERE enabled`,
so disabling the rows produced a verdict with ZERO gate results — which passed.
Measured against the test database: one cluster failing 6/6 gates promoted
cleanly once the rows were disabled, with `failed_gates == []` and a log line
reading `passed=True`.

It mattered because **retuning gates is a data operation by design**, so the
supported way to tune a gate was also the way to delete it. The partial case
was worse than the total one: drop `cross_source` alone and "authority cannot
self-corroborate" stops being enforced, invisibly.

These tests assert the NEGATIVES. The defect survived because everything in
the suite asserted that good input passes; nothing asserted that a broken gate
CONFIG fails.
"""
from __future__ import annotations

import pytest

from jarvis.discovery import gates
from jarvis.discovery.cluster import Cluster, Doc
from jarvis.discovery.gates import GateConfigError, GateResult, Verdict


def _doc(**kw):
    base = dict(signal_id=1, concept="ai agents", source_type="authority",
                source_name="yt_alex_hormozi", voice_id=None, terms=["ai", "agent"])
    base.update(kw)
    return Doc(**base)


def _awful() -> Cluster:
    """One member, authority-only, no distinct voices. Must fail every gate."""
    return Cluster(members=[_doc()], terms=["ai", "agent"])


# ---------------------------------------------------------------------------
# the all([]) trap
# ---------------------------------------------------------------------------

def test_a_verdict_with_no_gates_does_not_pass():
    """The whole defect, in one line. `all([])` is True."""
    assert Verdict(cluster_id=1, results=[]).passed is False


def test_a_verdict_with_gates_still_behaves():
    ok = GateResult("frequency", 9.0, 5.0, ">=", True)
    bad = GateResult("severity", 1.0, 4.0, ">=", False)
    assert Verdict(cluster_id=1, results=[ok]).passed is True
    assert Verdict(cluster_id=1, results=[ok, bad]).passed is False
    assert Verdict(cluster_id=1, results=[ok, bad]).failed_gates == ["severity"]


# ---------------------------------------------------------------------------
# the config must fail loudly, not quietly
# ---------------------------------------------------------------------------

async def test_disabling_every_gate_raises_instead_of_promoting(clean_db):
    """Reproduces the original finding end to end."""
    from jarvis import db

    v = await gates.evaluate(_awful(), recency_days=999.0)
    assert v.passed is False, "precondition: this cluster must fail on merit"
    assert len(v.results) == len(gates.REQUIRED_GATES)

    await db.execute("UPDATE gate_thresholds SET enabled = false")
    with pytest.raises(GateConfigError, match="disabled gate is not a passed gate"):
        await gates.evaluate(_awful(), recency_days=999.0)


async def test_disabling_ONE_gate_also_raises(clean_db):
    """The insidious case. `cross_source` is what stops the system building a
    product because one influencer said something compelling — losing it alone
    is quieter, and worse, than losing all six."""
    from jarvis import db

    await db.execute(
        "UPDATE gate_thresholds SET enabled = false WHERE gate = 'cross_source'")
    with pytest.raises(GateConfigError, match="cross_source"):
        await gates.evaluate(_awful(), recency_days=999.0)


async def test_the_error_names_the_missing_gates_and_the_fix(clean_db):
    """An operator reading this at 3am needs the gate name and what to do."""
    from jarvis import db

    await db.execute(
        "UPDATE gate_thresholds SET enabled = false WHERE gate IN ('severity','frequency')")
    with pytest.raises(GateConfigError) as e:
        await gates.evaluate(_awful(), recency_days=999.0)

    msg = str(e.value)
    assert "severity" in msg and "frequency" in msg
    assert "commercial_intent" not in msg, "must not blame gates that are fine"
    assert "tune its `threshold` value" in msg, "the message must say what to do instead"


async def test_a_misconfigured_gate_set_is_never_reported_as_a_failed_cluster(clean_db):
    """It must RAISE, not return a not-qualified verdict.

    Downgrading an operator error into "this cluster did not qualify" hides it
    behind a plausible result — the same move that let the forge mark two
    artifacts verified because their citations had been taken away.
    """
    from jarvis import db

    await db.execute("UPDATE gate_thresholds SET enabled = false")
    with pytest.raises(GateConfigError):
        await gates.evaluate(_awful(), recency_days=999.0)


# ---------------------------------------------------------------------------
# what must NOT have changed
# ---------------------------------------------------------------------------

async def test_tuning_a_threshold_value_is_still_free(clean_db):
    """Retuning is the knob this was always meant to expose. Only REMOVING a
    gate from the decision is refused."""
    from jarvis import db

    await db.execute("UPDATE gate_thresholds SET threshold = 0 "
                     "WHERE gate IN ('frequency','severity','distinct_voices',"
                     "'commercial_intent','cross_source')")
    await db.execute("UPDATE gate_thresholds SET threshold = 100000 "
                     "WHERE gate = 'recency_days'")

    v = await gates.evaluate(_awful(), recency_days=1.0)
    assert len(v.results) == len(gates.REQUIRED_GATES)
    assert v.passed is True, "with every threshold relaxed this must now qualify"


async def test_every_required_gate_is_seeded_by_the_migrations(clean_db):
    """If a migration ever renames a gate, this fails here rather than in
    production as a silently-shrinking gate set."""
    from jarvis import db

    rows = {r["gate"] for r in
            await db.fetch("SELECT gate FROM gate_thresholds WHERE enabled")}
    assert set(gates.REQUIRED_GATES) <= rows, \
        f"seeded gates are missing: {set(gates.REQUIRED_GATES) - rows}"
