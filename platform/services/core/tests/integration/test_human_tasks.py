"""Human tasks — including THE PHASE-2 EXIT CRITERION.

    "a real human task blocks and unblocks a run"

`test_a_human_task_blocks_and_unblocks_a_run` is that criterion, executable.
It runs a real step through the real engine against a real database.
"""
from __future__ import annotations

import pytest

from jarvis import db
from jarvis.console import human, tasks
from jarvis.runtime import engine, registry
from jarvis.runtime.registry import step
from jarvis.runtime.types import StepResult, StepStatus

THIS = "tests/integration/test_human_tasks.py"


@pytest.fixture(autouse=True)
def _reset():
    registry._reset_for_tests()
    yield
    registry._reset_for_tests()


# ===========================================================================
# THE EXIT CRITERION
# ===========================================================================

async def test_a_human_task_blocks_and_unblocks_a_run(clean_db):
    """order of events, all real:

    1. a step asks a human a question   → the run BLOCKS, visibly
    2. the operator answers             → the reply is parsed and typed
    3. the same step runs again         → it succeeds with the answer

    Resuming is just running the step again. There is no separate resume path
    to keep in sync and no callback that can be lost.
    """
    seen: dict = {}

    @step(id="h.ask", phase="TEST", test=THIS,
          acceptance=lambda r: bool(r.data.get("answer")),
          acceptance_desc="an answer was captured")
    async def ask(ctx, **kw):
        resp = await human.request(
            key=f"ask:{ctx.run_id}", title="What is the store id?",
            why="Offers cannot be created without it; the ladder cannot go live.",
            reply_schema={"type": "fields", "required": {"store_id": "str"}},
            run_id=ctx.run_id, step_id="h.ask")
        seen["state"] = resp.state
        if resp.blocked:
            return StepResult.blocked(resp.ref, "waiting for the store id")
        return StepResult.ok(answer=resp.value)

    run_id = await engine.create_run("TEST")
    ctx = await engine.open_context(run_id)

    # --- 1. blocks -------------------------------------------------------
    r1 = await engine.execute("h.ask", ctx)
    assert r1.status is StepStatus.BLOCKED_ON_HUMAN
    assert seen["state"] == "blocked"

    assert await db.fetchval("SELECT status FROM runs WHERE id=$1", run_id) \
        == "blocked_on_human", "the RUN must advertise that it is waiting on a person"

    task = await db.fetchrow("SELECT * FROM human_tasks WHERE status='open'")
    assert task is not None
    assert task["run_id"] == run_id and task["step_id"] == "h.ask"
    assert task["why"], "a task with no stated consequence gets ignored for weeks"

    # --- 2. the operator answers ----------------------------------------
    parsed = await tasks.apply_reply(int(task["id"]), "store_id: JPD_STORE_1")
    assert parsed.ok and parsed.value == {"store_id": "JPD_STORE_1"}
    assert await db.fetchval("SELECT status FROM human_tasks WHERE id=$1",
                             task["id"]) == "replied"

    # --- 3. re-running the SAME step now succeeds ------------------------
    r2 = await engine.execute("h.ask", ctx)
    assert r2.status is StepStatus.SUCCEEDED
    assert r2.data["answer"] == {"store_id": "JPD_STORE_1"}
    assert seen["state"] == "replied"

    assert await db.fetchval("SELECT status FROM runs WHERE id=$1", run_id) == "running", \
        "the run must stop advertising a block that has been answered"


async def test_a_blocked_step_does_not_post_a_second_card_on_retry(clean_db):
    """Every resume attempt spamming the topic would make it impossible to tell
    which card is live."""
    @step(id="h.idem", phase="TEST", test=THIS, acceptance=lambda r: True)
    async def ask(ctx, **kw):
        resp = await human.request(
            key="fixed-key", title="t", why="w",
            reply_schema={"type": "text", "min_chars": 5},
            run_id=ctx.run_id, step_id="h.idem")
        return (StepResult.blocked(resp.ref, "waiting") if resp.blocked
                else StepResult.ok(answer=resp.text))

    ctx = await engine.open_context(await engine.create_run("TEST"))
    for _ in range(3):
        assert (await engine.execute("h.idem", ctx)).status is StepStatus.BLOCKED_ON_HUMAN

    assert await db.fetchval("SELECT count(*) FROM human_tasks") == 1


async def test_skip_releases_the_block_as_a_decision_not_a_failure(clean_db):
    @step(id="h.skippable", phase="TEST", test=THIS,
          acceptance=lambda r: True)
    async def ask(ctx, **kw):
        resp = await human.request(
            key="skip-key", title="Paste the Sintra output", why="blocking variant B",
            reply_schema={"type": "text", "min_chars": 200},
            run_id=ctx.run_id, step_id="h.skippable")
        if resp.blocked:
            return StepResult.blocked(resp.ref, "waiting")
        if resp.state == "skipped":
            return StepResult(status=StepStatus.SKIPPED_DORMANT,
                              reason=f"operator skipped: {resp.reason}")
        return StepResult.ok(answer=resp.text)

    ctx = await engine.open_context(await engine.create_run("TEST"))
    await engine.execute("h.skippable", ctx)

    tid = await db.fetchval("SELECT id FROM human_tasks WHERE status='open'")
    parsed = await tasks.apply_reply(int(tid), "SKIP sintra is Cloudflare-blocked today")
    assert parsed.skipped

    r = await engine.execute("h.skippable", ctx)
    assert r.status is StepStatus.SKIPPED_DORMANT
    assert "Cloudflare-blocked" in r.reason
    assert await db.fetchval("SELECT skip_reason FROM human_tasks WHERE id=$1", tid) \
        == "sintra is Cloudflare-blocked today"


# ===========================================================================
# the task survives Telegram being down
# ===========================================================================

async def test_a_task_is_created_even_when_telegram_is_dormant(clean_db):
    """🔴 The invariant. The row is written FIRST; posting is a separate,
    retryable act.

    If posting came first, an outage would make work vanish. Here only the
    notification is missing, the run is still visibly blocked, and the failure
    to announce is itself recorded.
    """
    t = await tasks.create(
        type="task", title="Do the thing", why="it blocks the ladder",
        reply_schema={"type": "text", "min_chars": 5}, run_id=None)

    row = await db.fetchrow("SELECT * FROM human_tasks WHERE id=$1", t.id)
    assert row["status"] == "open"
    assert row["telegram_message_id"] is None
    assert "card not posted" in (row["last_parse_error"] or "")

    # And it is visible to the operator regardless.
    assert [x["ref"] for x in await tasks.open_tasks()] == [t.ref]


async def test_post_pending_retries_unannounced_cards(clean_db):
    await tasks.create(type="task", title="a", why="w",
                       reply_schema={"type": "text", "min_chars": 1})
    # Telegram is still dormant, so nothing can be posted — but the sweep must
    # run cleanly rather than raising, or the housekeeping loop dies.
    assert await tasks.post_pending() == 0


async def test_a_task_requires_a_stated_consequence(clean_db):
    with pytest.raises(ValueError, match="WHY"):
        await tasks.create(type="task", title="t", why="   ",
                           reply_schema={"type": "text", "min_chars": 1})


# ===========================================================================
# replies
# ===========================================================================

async def test_a_bad_reply_re_asks_and_persists_nothing(clean_db):
    t = await tasks.create(type="task", title="t", why="w",
                           reply_schema={"type": "text", "min_chars": 50})

    parsed = await tasks.apply_reply(t.id, "nope")
    assert parsed.ok is False

    row = await db.fetchrow("SELECT * FROM human_tasks WHERE id=$1", t.id)
    assert row["status"] == "open", "a rejected reply must NOT resolve the task"
    assert row["reply_json"] is None, "a half-answer must never be stored"
    assert row["reply_attempts"] == 1
    assert "too short" in row["last_parse_error"]

    good = "x" * 60
    assert (await tasks.apply_reply(t.id, good)).ok
    row = await db.fetchrow("SELECT * FROM human_tasks WHERE id=$1", t.id)
    assert row["status"] == "replied"
    assert row["last_parse_error"] is None


async def test_a_resolved_task_will_not_accept_a_second_reply(clean_db):
    t = await tasks.create(type="task", title="t", why="w",
                           reply_schema={"type": "text", "min_chars": 3})
    assert (await tasks.apply_reply(t.id, "yes please")).ok
    second = await tasks.apply_reply(t.id, "actually no")
    assert second.ok is False
    assert "already replied" in second.error


# ===========================================================================
# expiry
# ===========================================================================

async def test_expiry_is_announced_not_silently_dropped(clean_db):
    """An expired approval silently stalled a Pimlico build for five days."""
    t = await tasks.create(type="task", title="Approve the launch", why="blocks publish",
                           reply_schema={"type": "choice", "options": ["yes", "no"]})
    await db.execute("UPDATE human_tasks SET expires_at = now() - interval '2 hours', "
                     "created_at = now() - interval '30 hours' WHERE id=$1", t.id)

    out = await tasks.expire_due()
    assert len(out) == 1 and out[0]["ref"] == t.ref
    assert out[0]["age_hours"] >= 29
    assert await db.fetchval("SELECT status FROM human_tasks WHERE id=$1", t.id) == "expired"


async def test_an_empty_expiry_sweep_still_records_success(clean_db):
    """The sweep ran and found nothing — that is a SUCCESS, not a non-event.

    Recording success only when something expired left last_success_at NULL on
    the normal path, so console.expire_tasks read as never-run and any
    staleness alert on it was either blind or permanently firing.
    """
    await db.execute("UPDATE job_registry SET last_success_at = NULL "
                     "WHERE job_name = 'console.expire_tasks'")
    out = await tasks.expire_due()
    assert out == []
    assert await db.fetchval(
        "SELECT last_success_at FROM job_registry "
        "WHERE job_name = 'console.expire_tasks'") is not None


async def test_an_expired_task_still_blocks_its_step(clean_db):
    """Proceeding on a deadline would be the worst possible reading of it —
    the work is still not done."""
    t = await tasks.create(type="task", title="t", why="w",
                           reply_schema={"type": "text", "min_chars": 3},
                           idempotency_key="expiring")
    await db.execute("UPDATE human_tasks SET status='expired' WHERE id=$1", t.id)

    resp = await human.request(key="expiring", title="t", why="w",
                               reply_schema={"type": "text", "min_chars": 3})
    assert resp.state == "blocked"


async def test_reopen_puts_an_expired_task_back_in_the_queue(clean_db):
    t = await tasks.create(type="task", title="t", why="w",
                           reply_schema={"type": "text", "min_chars": 3})
    await db.execute("UPDATE human_tasks SET status='expired' WHERE id=$1", t.id)
    assert await tasks.reopen(t.ref) is True
    assert [x["ref"] for x in await tasks.open_tasks()] == [t.ref]


# ===========================================================================
# decisions and the Sintra bridge
# ===========================================================================

async def test_a_decision_card_carries_its_options(clean_db):
    resp = await human.decide(
        key="dec-1", question="Publish the ladder?",
        why="Outward-facing and irreversible — three offers go live.",
        options=["approve", "reject"], context={"solution": 7, "price": "€297"})
    assert resp.state == "blocked"

    row = await db.fetchrow("SELECT * FROM human_tasks WHERE ref=$1", resp.ref)
    assert row["type"] == "decision"
    assert row["stream"] == "decisions"
    assert row["options"] == ["approve", "reject"] or row["options"] == '["approve", "reject"]'

    tid = int(row["id"])
    assert (await tasks.apply_reply(tid, "1")).value == {"choice": "approve"}


async def test_a_decision_needs_at_least_two_options(clean_db):
    with pytest.raises(ValueError, match="at least two"):
        await human.decide(key="d", question="q", why="w", options=["only"])


async def test_the_sintra_card_goes_to_its_own_stream(clean_db):
    """The dedicated Sintra thread — a clean list of prompts, each replyable in
    place so the parser can match a response to its task."""
    resp = await human.sintra(
        key="sin-1", why="Blocking sales copy variant B for AP-1042",
        bot="Aria", prompt="You are writing ad copy...\nAUDIENCE: contractors",
        min_chars=50)
    row = await db.fetchrow("SELECT * FROM human_tasks WHERE ref=$1", resp.ref)
    assert row["type"] == "sintra"
    assert row["stream"] == "sintra"
    assert row["ref"].startswith("SIN-")
    assert "AUDIENCE: contractors" in row["how_md"]
    assert row["verify_command"] == f"jpd tasks show {resp.ref}"


async def test_sintra_output_that_is_an_error_message_is_refused(clean_db):
    """🔴 Nothing Sintra-shaped can auto-publish — now enforced on the human
    path too, because the operator can paste whatever the UI showed them."""
    resp = await human.sintra(key="sin-2", why="w", bot="Aria", prompt="p",
                              min_chars=50)
    tid = await db.fetchval("SELECT id FROM human_tasks WHERE ref=$1", resp.ref)

    bad = "[Automation failed: Page.goto: Timeout 30000ms exceeded]" + " " * 100
    parsed = await tasks.apply_reply(int(tid), bad)
    assert parsed.ok is False
    assert "error message, not output" in parsed.error
    assert await db.fetchval("SELECT status FROM human_tasks WHERE id=$1", tid) == "open"
