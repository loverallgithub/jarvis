"""Checkpoints, resume, and THE RESUME RULE.

The phase-0 exit criterion lives in this file:
``test_resume_works_on_an_empty_database``.
"""
from __future__ import annotations

import pytest

from jarvis import db
from jarvis.runtime import checkpoints, engine, registry
from jarvis.runtime.registry import step
from jarvis.runtime.types import Evidence, StepResult, StepStatus

THIS = "tests/integration/test_checkpoints.py"


@pytest.fixture(autouse=True)
def _steps():
    registry._reset_for_tests()

    @step(id="c.two_evidence", phase="TEST", test=THIS,
          acceptance=lambda r: len(r.evidence) >= 2,
          acceptance_desc="at least 2 evidence rows")
    async def two_evidence(ctx, n: int = 2, **kw):
        return StepResult(status=StepStatus.SUCCEEDED,
                          evidence=[Evidence(sha256=f"h{i}") for i in range(n)])

    yield
    registry._reset_for_tests()


# ---------------------------------------------------------------------------
# THE PHASE-0 EXIT CRITERION
# ---------------------------------------------------------------------------

async def test_resume_works_on_an_empty_database(clean_db):
    """`jpd resume` must work before there is anything to resume.

    Pimlico's status tooling assumed rows existed and threw when they did not,
    so it was least usable exactly when it was most needed — at the start, and
    after a wipe. An empty database is a legitimate state.
    """
    report = await checkpoints.resume_report()

    assert report["empty"] is True
    assert report["runs"] == []
    assert report["resumable"] == []
    assert report["checkpoint"] is None
    assert isinstance(report["open_human_tasks"], list)
    assert isinstance(report["non_live_connectors"], list)

    md = checkpoints.render_markdown(report)
    assert "State: EMPTY" in md
    assert "legitimate state" in md


async def test_cli_resume_exits_zero_on_an_empty_database(clean_db, capsys):
    """The exit criterion as the operator actually experiences it."""
    from jarvis.cli import cmd_resume

    class A:
        run = None
        json = False

    assert await cmd_resume(A()) == 0
    out = capsys.readouterr().out
    assert "the database is empty" in out


# ---------------------------------------------------------------------------
# writing and reading
# ---------------------------------------------------------------------------

async def test_a_checkpoint_must_state_why(clean_db):
    """A checkpoint with no reason cannot be judged on resume."""
    with pytest.raises(ValueError, match="why"):
        await checkpoints.write(phase="TEST", label="x", reason="   ")


async def test_checkpoint_round_trip(clean_db):
    run_id = await engine.create_run("TEST")
    cid = await checkpoints.write(
        phase="TEST", label="before-human-task", reason="blocking on HT-001",
        run_id=run_id, state={"cursor": 12}, resumable_from="c.two_evidence")

    latest = await checkpoints.latest(run_id)
    assert latest["id"] == cid
    assert latest["state_json"] == {"cursor": 12} or latest["state_json"] == '{"cursor": 12}'
    assert latest["resumable_from"] == "c.two_evidence"


async def test_resume_surfaces_a_run_and_its_last_step(clean_db):
    run_id = await engine.create_run("TEST")
    ctx = await engine.open_context(run_id)
    await engine.execute("c.two_evidence", ctx, n=2)

    report = await checkpoints.resume_report()
    assert report["empty"] is False
    assert len(report["resumable"]) == 1
    r = report["resumable"][0]
    assert r["run_id"] == run_id
    assert r["last_step"]["step_id"] == "c.two_evidence"
    assert r["last_step"]["status"] == "succeeded"
    assert r["lease_expired"] is False, "the lease is live — another worker holds this run"


# ---------------------------------------------------------------------------
# THE RESUME RULE
# ---------------------------------------------------------------------------

async def test_verify_last_agrees_when_the_stored_verdict_still_holds(clean_db):
    run_id = await engine.create_run("TEST")
    ctx = await engine.open_context(run_id)
    await engine.execute("c.two_evidence", ctx, n=2)

    out = await checkpoints.verify_last()
    assert out["verdict"] == "agrees"
    assert out["stored_accepted"] is True
    assert out["reevaluated_accepted"] is True


async def test_verify_last_disagrees_when_the_predicate_changes_under_it(clean_db):
    """The case the rule exists for.

    A step recorded 'succeeded'. The acceptance bar then moves — a stricter
    predicate, a tightened threshold, a URL that has since died. Re-running the
    predicate against the PERSISTED result reveals that the stored DONE line
    can no longer be trusted. Pimlico's [T-1.12] was the mirror image: a
    ledger line read IN-PROGRESS while the work was complete, and the work was
    expensively redone.
    """
    run_id = await engine.create_run("TEST")
    ctx = await engine.open_context(run_id)
    await engine.execute("c.two_evidence", ctx, n=2)
    assert (await checkpoints.verify_last())["verdict"] == "agrees"

    # Raise the bar, exactly as a later commit would.
    registry._reset_for_tests()

    @step(id="c.two_evidence", phase="TEST", test=THIS,
          acceptance=lambda r: len(r.evidence) >= 10,
          acceptance_desc="at least 10 evidence rows")
    async def stricter(ctx, **kw):
        return StepResult.ok()

    out = await checkpoints.verify_last()
    assert out["verdict"] == "disagrees"
    assert out["stored_accepted"] is True
    assert out["reevaluated_accepted"] is False
    assert "do not trust" in out["detail"].lower()


async def test_verify_last_says_unverifiable_rather_than_assuming_success(clean_db):
    """An unregistered step must NOT be reported as fine. Saying 'I cannot
    check this' is the honest answer and the whole point of the rule."""
    run_id = await engine.create_run("TEST")
    ctx = await engine.open_context(run_id)
    await engine.execute("c.two_evidence", ctx, n=2)

    registry._reset_for_tests()
    out = await checkpoints.verify_last()
    assert out["verdict"] == "unverifiable"
    assert "not registered" in out["detail"]


async def test_verify_last_on_an_empty_database(clean_db):
    out = await checkpoints.verify_last()
    assert out["verdict"] == "no_steps"


async def test_cli_verify_exit_code_is_2_on_disagreement(clean_db):
    """A script must be able to branch on 'do not trust this'."""
    from jarvis.cli import cmd_verify

    run_id = await engine.create_run("TEST")
    ctx = await engine.open_context(run_id)
    await engine.execute("c.two_evidence", ctx, n=2)

    class A:
        run = None
        json = True
        last = True

    assert await cmd_verify(A()) == 0

    registry._reset_for_tests()

    @step(id="c.two_evidence", phase="TEST", test=THIS,
          acceptance=lambda r: len(r.evidence) >= 99)
    async def stricter(ctx, **kw):
        return StepResult.ok()

    assert await cmd_verify(A()) == 2


# ---------------------------------------------------------------------------
# generated markdown
# ---------------------------------------------------------------------------

async def test_regeneration_preserves_the_hand_written_section(clean_db):
    """Blowing away hand-written reasoning on every regeneration is how a
    generated doc becomes worthless."""
    report = await checkpoints.resume_report()
    first = checkpoints.render_markdown(report, "## Why\n\nBecause the ladder degrades gracefully.")
    why = checkpoints.split_why(first)
    assert "degrades gracefully" in why

    second = checkpoints.render_markdown(report, why)
    assert "degrades gracefully" in second
    assert second.count(checkpoints.WHY_MARKER) == 1
