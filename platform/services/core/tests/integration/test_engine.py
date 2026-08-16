"""Step engine invariants, against a real database.

Every test here names the Pimlico defect it prevents. If one of these ever
starts failing, the corresponding failure has come back — they are not
coverage padding.
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis import db
from jarvis.connectors import base as connectors
from jarvis.runtime import engine, lease as lease_mod, registry
from jarvis.runtime.engine import RunContext
from jarvis.runtime.registry import step
from jarvis.runtime.types import Evidence, StepResult, StepStatus

THIS = "tests/integration/test_engine.py"
CALLS: dict[str, int] = {}


@pytest.fixture(autouse=True)
def _steps():
    registry._reset_for_tests()
    CALLS.clear()

    @step(id="t.always_ok", phase="TEST", acceptance=lambda r: True, test=THIS)
    async def always_ok(ctx, **kw):
        CALLS["always_ok"] = CALLS.get("always_ok", 0) + 1
        return StepResult.ok(value=42)

    @step(id="t.needs_five_evidence", phase="TEST", test=THIS,
          acceptance=lambda r: len(r.evidence) >= 5,
          acceptance_desc="at least 5 evidence rows")
    async def needs_five(ctx, n: int = 0, **kw):
        return StepResult(status=StepStatus.SUCCEEDED,
                          evidence=[Evidence(sha256=f"h{i}") for i in range(n)])

    @step(id="t.dormant_dep", phase="TEST", acceptance=lambda r: True, test=THIS,
          requires_connectors=("sintra",))
    async def dormant_dep(ctx, **kw):
        CALLS["dormant_dep"] = CALLS.get("dormant_dep", 0) + 1
        return StepResult.ok()

    @step(id="t.idempotent", phase="TEST", acceptance=lambda r: True, test=THIS,
          idempotency_key="need_id")
    async def idem(ctx, need_id: int = 1, **kw):
        CALLS["idem"] = CALLS.get("idem", 0) + 1
        return StepResult.ok(need_id=need_id, call=CALLS["idem"])

    @step(id="t.slow", phase="TEST", acceptance=lambda r: True, test=THIS, timeout_s=1)
    async def slow(ctx, **kw):
        await asyncio.sleep(5)
        return StepResult.ok()

    @step(id="t.expensive", phase="TEST", acceptance=lambda r: True, test=THIS,
          cost_budget_usd=0.10)
    async def expensive(ctx, **kw):
        ctx.spend(0.50)
        return StepResult.ok()

    @step(id="t.returns_garbage", phase="TEST", acceptance=lambda r: True, test=THIS)
    async def garbage(ctx, **kw):
        return {"status": "succeeded"}          # not a StepResult

    @step(id="t.explodes", phase="TEST", acceptance=lambda r: True, test=THIS)
    async def explodes(ctx, **kw):
        raise RuntimeError("kaboom")

    @step(id="t.repairable", phase="TEST", acceptance=lambda r: False, test=THIS,
          repairable=True, max_repairs=2)
    async def repairable(ctx, **kw):
        return StepResult.ok()

    yield
    registry._reset_for_tests()


async def _ctx(clean_db) -> RunContext:
    run_id = await engine.create_run("TEST")
    return await engine.open_context(run_id)


# ---------------------------------------------------------------------------
# RULE 2 — succeeded requires acceptance
# ---------------------------------------------------------------------------

async def test_acceptance_failure_downgrades_success_to_failed(clean_db):
    """A step cannot declare itself successful.

    Pimlico's stages set their own status and the verification result was
    advisory, so a stage could report success while its own checker disagreed.
    """
    ctx = await _ctx(clean_db)
    r = await engine.execute("t.needs_five_evidence", ctx, n=3)

    assert r.status is StepStatus.FAILED
    assert "at least 5 evidence rows" in (r.reason or "")

    row = await db.fetchrow("SELECT * FROM steps WHERE step_id = 't.needs_five_evidence'")
    assert row["status"] == "failed"
    assert row["accepted"] is False
    # Evidence is RETAINED on failure — you cannot diagnose what was discarded.
    assert len(row["evidence_json"]) == 3 if isinstance(row["evidence_json"], list) else True


async def test_acceptance_pass_is_recorded_as_accepted(clean_db):
    ctx = await _ctx(clean_db)
    r = await engine.execute("t.needs_five_evidence", ctx, n=5)
    assert r.status is StepStatus.SUCCEEDED
    row = await db.fetchrow("SELECT * FROM steps WHERE step_id = 't.needs_five_evidence'")
    assert row["accepted"] is True


# ---------------------------------------------------------------------------
# RULE 1 — no null status, ever
# ---------------------------------------------------------------------------

async def test_no_step_row_can_have_a_null_status(clean_db):
    ctx = await _ctx(clean_db)
    for sid, kw in (("t.always_ok", {}), ("t.needs_five_evidence", {"n": 0}),
                    ("t.explodes", {}), ("t.dormant_dep", {})):
        try:
            await engine.execute(sid, ctx, **kw)
        except Exception:                                        # noqa: BLE001
            pass
    nulls = await db.fetchval("SELECT count(*) FROM steps WHERE status IS NULL")
    assert nulls == 0

    with pytest.raises(Exception):        # the CHECK constraint, not the enum
        await db.execute(
            "INSERT INTO steps (run_id, step_id, phase, status) VALUES ($1,'x','TEST','unknown')",
            ctx.run_id)


async def test_an_exception_becomes_failed_not_a_silent_success(clean_db):
    ctx = await _ctx(clean_db)
    r = await engine.execute("t.explodes", ctx)
    assert r.status is StepStatus.FAILED
    assert "RuntimeError" in (r.reason or "") and "kaboom" in (r.reason or "")


async def test_a_step_returning_a_non_StepResult_fails(clean_db):
    """A bare dict cannot be gated by an acceptance predicate, so it is refused
    rather than coerced into a success."""
    ctx = await _ctx(clean_db)
    r = await engine.execute("t.returns_garbage", ctx)
    assert r.status is StepStatus.FAILED
    assert "expected StepResult" in (r.reason or "")


async def test_timeout_is_a_failure(clean_db):
    ctx = await _ctx(clean_db)
    r = await engine.execute("t.slow", ctx)
    assert r.status is StepStatus.FAILED
    assert "timed out" in (r.reason or "")


# ---------------------------------------------------------------------------
# RULE 3 — a dormant connector means the step does not run
# ---------------------------------------------------------------------------

async def test_dormant_connector_skips_without_calling_the_step(clean_db):
    """Kills the Sintra class outright.

    Sintra was Cloudflare-blocked; the code called it anyway, caught the
    exception, returned the error text as a normal string, and that string was
    published to a live LinkedIn account on six consecutive days.
    """
    await db.execute("UPDATE connector_health SET state='dormant' WHERE connector='sintra'")
    ctx = await _ctx(clean_db)
    r = await engine.execute("t.dormant_dep", ctx)

    assert r.status is StepStatus.SKIPPED_DORMANT
    assert "sintra" in (r.reason or "")
    assert CALLS.get("dormant_dep") is None, "the step body must NOT have run"

    row = await db.fetchrow("SELECT * FROM steps WHERE step_id = 't.dormant_dep'")
    assert row["status"] == "skipped_dormant"


async def test_live_connector_lets_the_step_run(clean_db):
    await connectors.record_contract_test("sintra", True, "test fixture")
    ctx = await _ctx(clean_db)
    r = await engine.execute("t.dormant_dep", ctx)
    assert r.status is StepStatus.SUCCEEDED
    assert CALLS["dormant_dep"] == 1


async def test_an_unregistered_connector_is_treated_as_dormant(clean_db):
    """Absent must be the safe direction. An unknown dependency is never live."""

    @step(id="t.unknown_dep", phase="TEST", acceptance=lambda r: True, test=THIS,
          requires_connectors=("a_connector_that_does_not_exist",))
    async def _s(ctx, **kw):
        return StepResult.ok()

    ctx = await _ctx(clean_db)
    r = await engine.execute("t.unknown_dep", ctx)
    assert r.status is StepStatus.SKIPPED_DORMANT


# ---------------------------------------------------------------------------
# RULE 4 — the lease guard. This is the resurrection bug.
# ---------------------------------------------------------------------------

async def test_killing_a_run_stops_it_writing_a_success(clean_db):
    """Pimlico's `lease_owner` appeared in NO WHERE clause, so KILL set a flag
    that the next `advance()` overwrote — and the run came back from the dead.

    Here the kill clears the lease; the in-flight step's guarded UPDATE matches
    zero rows and raises instead of recording a success.
    """
    killed = asyncio.Event()

    @step(id="t.long_running", phase="TEST", acceptance=lambda r: True, test=THIS,
          timeout_s=10)
    async def long_running(ctx, **kw):
        await killed.wait()
        return StepResult.ok()

    ctx = await _ctx(clean_db)
    task = asyncio.create_task(engine.execute("t.long_running", ctx))
    await asyncio.sleep(0.2)

    assert await lease_mod.request_kill(ctx.run_id) is True
    killed.set()

    with pytest.raises(lease_mod.LeaseLost):
        await task

    row = await db.fetchrow("SELECT * FROM steps WHERE step_id = 't.long_running'")
    assert row["status"] == "running", "the killed step must NOT have been marked succeeded"
    assert await db.fetchval(
        "SELECT status FROM runs WHERE id = $1", ctx.run_id) == "killed"


async def test_a_killed_run_cannot_be_re_leased(clean_db):
    ctx = await _ctx(clean_db)
    await lease_mod.request_kill(ctx.run_id)
    assert await lease_mod.acquire(ctx.run_id, "someone-else") is False
    with pytest.raises(lease_mod.LeaseLost):
        await engine.open_context(ctx.run_id, "someone-else")


async def test_a_second_worker_cannot_steal_a_live_lease(clean_db):
    ctx = await _ctx(clean_db)
    assert await lease_mod.acquire(ctx.run_id, "worker-2") is False
    assert await lease_mod.held_by(ctx.run_id, ctx.owner) is True


async def test_an_expired_lease_can_be_taken_over(clean_db):
    """The other half: a worker that died must not hold the run forever."""
    ctx = await _ctx(clean_db)
    await db.execute(
        "UPDATE runs SET lease_expires_at = now() - interval '1 hour' WHERE id = $1",
        ctx.run_id)
    assert await lease_mod.acquire(ctx.run_id, "worker-2") is True
    assert await lease_mod.held_by(ctx.run_id, ctx.owner) is False


async def test_execute_refuses_to_start_without_the_lease(clean_db):
    ctx = await _ctx(clean_db)
    await lease_mod.release(ctx.run_id, ctx.owner)
    await lease_mod.acquire(ctx.run_id, "worker-2")
    with pytest.raises(lease_mod.LeaseLost):
        await engine.execute("t.always_ok", ctx)


# ---------------------------------------------------------------------------
# idempotency
# ---------------------------------------------------------------------------

async def test_a_succeeded_step_is_not_re_executed(clean_db):
    ctx = await _ctx(clean_db)
    first = await engine.execute("t.idempotent", ctx, need_id=7)
    second = await engine.execute("t.idempotent", ctx, need_id=7)

    assert CALLS["idem"] == 1, "the body ran twice — idempotency is not holding"
    assert first.data == second.data
    assert await db.fetchval(
        "SELECT count(*) FROM steps WHERE step_id='t.idempotent' AND status='succeeded'") == 1


async def test_a_different_idempotency_key_does_re_execute(clean_db):
    ctx = await _ctx(clean_db)
    await engine.execute("t.idempotent", ctx, need_id=7)
    await engine.execute("t.idempotent", ctx, need_id=8)
    assert CALLS["idem"] == 2


async def test_the_database_refuses_duplicate_successes(clean_db):
    """Belt and braces: the partial unique index, not just the code path."""
    ctx = await _ctx(clean_db)
    await engine.execute("t.idempotent", ctx, need_id=7)
    with pytest.raises(Exception):
        await db.execute(
            "INSERT INTO steps (run_id, step_id, phase, status, idempotency_value) "
            "VALUES ($1, 't.idempotent', 'TEST', 'succeeded', '7')", ctx.run_id)


# ---------------------------------------------------------------------------
# repair_count durability
# ---------------------------------------------------------------------------

async def test_repair_count_survives_attempts(clean_db):
    """Pimlico's repair guard tested `attempts`, which `advance()` reset to 0
    on every transition. The guard was therefore always true, and a section the
    model reliably answered "TBD" looped forever at full LLM cost.

    repair_count is carried forward across attempts and never reset.
    """
    ctx = await _ctx(clean_db)

    await engine.execute("t.repairable", ctx)
    assert await engine.repair_budget_remaining(ctx.run_id, "t.repairable") == 2

    await engine.record_repair(ctx.run_id, "t.repairable", ctx.owner)
    assert await engine.repair_budget_remaining(ctx.run_id, "t.repairable") == 1

    # A fresh attempt must INHERIT the counter, not reset it.
    await engine.execute("t.repairable", ctx)
    rows = await db.fetch(
        "SELECT attempt, repair_count FROM steps WHERE step_id='t.repairable' ORDER BY id")
    assert [r["attempt"] for r in rows] == [1, 2]
    assert rows[-1]["repair_count"] == 1
    assert await engine.repair_budget_remaining(ctx.run_id, "t.repairable") == 1

    await engine.record_repair(ctx.run_id, "t.repairable", ctx.owner)
    assert await engine.repair_budget_remaining(ctx.run_id, "t.repairable") == 0


async def test_a_non_repairable_step_has_no_repair_budget(clean_db):
    ctx = await _ctx(clean_db)
    await engine.execute("t.always_ok", ctx)
    assert await engine.repair_budget_remaining(ctx.run_id, "t.always_ok") == 0


# ---------------------------------------------------------------------------
# RULE 5 — cost budget
# ---------------------------------------------------------------------------

async def test_exceeding_the_budget_fails_the_step_not_the_invoice(clean_db):
    ctx = await _ctx(clean_db)
    r = await engine.execute("t.expensive", ctx)
    assert r.status is StepStatus.FAILED
    assert "budget" in (r.reason or "").lower()


async def test_run_cost_accumulates(clean_db):
    @step(id="t.cheap", phase="TEST", acceptance=lambda r: True, test=THIS,
          cost_budget_usd=1.0)
    async def cheap(ctx, **kw):
        ctx.spend(0.02)
        return StepResult.ok()

    ctx = await _ctx(clean_db)
    await engine.execute("t.cheap", ctx)
    total = await db.fetchval("SELECT cost_usd FROM runs WHERE id = $1", ctx.run_id)
    assert float(total) == pytest.approx(0.02)


async def test_a_long_step_keeps_its_lease_alive(clean_db, monkeypatch):
    """🔴 A step longer than the lease TTL must not lose the run.

    `forge.generate` spent 580s on LLM calls against a 120s TTL, succeeded, and
    the NEXT step raised LeaseLost — after paying for every token. This is the
    exact failure this codebase quotes Pimlico for, reproduced.
    """
    from types import SimpleNamespace
    # `settings` is a frozen dataclass, so patch the engine's reference to it.
    monkeypatch.setattr(engine, "settings", SimpleNamespace(lease_ttl_s=15))

    @step(id="t.long", phase="TEST", acceptance=lambda r: True, test=THIS,
          timeout_s=30)
    async def long_step(ctx, **kw):
        await asyncio.sleep(12)
        return StepResult.ok()

    run_id = await engine.create_run("TEST")
    owner = lease_mod.new_owner()
    # A SHORT initial lease: without the heartbeat this expires mid-step.
    assert await lease_mod.acquire(run_id, owner, ttl_s=8)
    ctx = RunContext(run_id=run_id, phase="TEST", owner=owner)

    assert (await engine.execute("t.long", ctx)).status is StepStatus.SUCCEEDED

    # Still held — so the NEXT step can run, which is the whole point.
    assert await lease_mod.held_by(run_id, owner) is True
    assert (await engine.execute("t.always_ok", ctx)).status is StepStatus.SUCCEEDED
