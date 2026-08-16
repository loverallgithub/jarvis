"""Scheduler — the tick that makes job_registry intervals real.

The failure this guards against is the one observed live on 2026-08-16:
five jobs with intervals in job_registry and nobody running them, so the
pipeline sat idle for 8 days and the platform alarmed about its own stall.
"""
from __future__ import annotations

import pytest

from jarvis import db
from jarvis.runtime import scheduler


async def _seed(job: str, interval_s: int, *, success_ago_s=None, attempt_ago_s=None):
    await db.execute(
        """
        INSERT INTO job_registry (job_name, expected_interval_s, last_success_at,
                                  last_attempt_at)
        VALUES ($1, $2,
                CASE WHEN $3::int IS NULL THEN NULL
                     ELSE now() - $3 * interval '1 second' END,
                CASE WHEN $4::int IS NULL THEN NULL
                     ELSE now() - $4 * interval '1 second' END)
        ON CONFLICT (job_name) DO UPDATE
          SET expected_interval_s = EXCLUDED.expected_interval_s,
              last_success_at = EXCLUDED.last_success_at,
              last_attempt_at = EXCLUDED.last_attempt_at,
              enabled = true
        """, job, interval_s, success_ago_s, attempt_ago_s)


async def test_overdue_dispatchable_jobs_are_due(clean_db):
    await _seed("discovery.funnel", 86400, success_ago_s=2 * 86400)
    assert "discovery.funnel" in await scheduler.due_jobs()


async def test_a_fresh_job_is_not_due(clean_db):
    await _seed("discovery.funnel", 86400, success_ago_s=60)
    assert "discovery.funnel" not in await scheduler.due_jobs()


async def test_never_run_jobs_are_due_immediately(clean_db):
    """NULL last_success_at must read as 'never', which is WORSE than stale —
    the same rule as never-verified alert rules."""
    await _seed("alert.synthetic_sweep", 604800)
    assert "alert.synthetic_sweep" in await scheduler.due_jobs()


async def test_recent_failed_attempt_backs_off(clean_db):
    """A failing daily job must not be retried by every 15-minute tick —
    for the spending jobs that is a money-burn loop."""
    await _seed("forge.build", 86400, success_ago_s=3 * 86400, attempt_ago_s=600)
    assert "forge.build" not in await scheduler.due_jobs()

    await _seed("forge.build", 86400, success_ago_s=3 * 86400,
                attempt_ago_s=8 * 3600)
    assert "forge.build" in await scheduler.due_jobs()


async def test_app_loop_jobs_are_never_dispatched(clean_db):
    """poll_replies etc. already run in an app loop; driving them from the
    tick as well would double-drive them."""
    await _seed("console.poll_replies", 60, success_ago_s=86400)
    assert "console.poll_replies" not in await scheduler.due_jobs()


async def test_research_with_nothing_to_do_stamps_success(clean_db):
    """'Every promoted need has a dossier' is research being DONE, not stale —
    the same rule as the empty expiry sweep."""
    await _seed("research.dossier", 86400, success_ago_s=3 * 86400)
    out = await scheduler._run_research()
    assert out == {"nothing_to_do": True}
    age = await db.fetchval(
        "SELECT now() - last_success_at FROM job_registry "
        "WHERE job_name = 'research.dossier'")
    assert age is not None and age.total_seconds() < 60


async def test_only_one_spending_job_runs_per_tick(clean_db, monkeypatch):
    ran: list[str] = []

    async def fake_research():
        ran.append("research")
        return {"need_id": 1}

    async def fake_forge():
        ran.append("forge")
        return {"need_id": 1}

    monkeypatch.setitem(scheduler.DISPATCH, "research.dossier", fake_research)
    monkeypatch.setitem(scheduler.DISPATCH, "forge.build", fake_forge)
    await _seed("research.dossier", 86400, success_ago_s=3 * 86400)
    await _seed("forge.build", 86400, success_ago_s=3 * 86400)
    # Freeze the free jobs so only the two spenders are due.
    for j in ("alert.synthetic_sweep", "commerce.artifact_sweep", "discovery.funnel"):
        await _seed(j, 86400, success_ago_s=1)

    out = await scheduler.tick()
    assert ran == ["forge"] or ran == ["research"]
    deferred = [j for j, r in out.items() if "deferred" in r]
    assert len(deferred) == 1
