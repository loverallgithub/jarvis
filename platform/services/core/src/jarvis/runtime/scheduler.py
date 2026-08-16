"""Autonomous job scheduler — makes job_registry intervals real.

Until 2026-08-16 the intervals in `job_registry` were documentation, not
behaviour: only the in-app loops (connector sweep, harvest, reply polling,
task expiry) ran unattended. `discovery.funnel`, `research.dossier`,
`forge.build`, `alert.synthetic_sweep` and `commerce.artifact_sweep` moved
only when an operator typed the command — so the platform stalled the moment
the operator stepped away, and then correctly alarmed about its own stall
(that is exactly what the 2026-08-16 AlertNeverTripped firing was).

Invoked by `jpd scheduler tick` from the host timer `jarvis-scheduler.timer`.
All decisions live HERE, in container code; the host side is nothing but
flock + `jpd`, so there is no logic on the host to drift (the bin/jpd rule).

Three rules the tick enforces:

**1. At most ONE spending job per tick.** `research.dossier` and `forge.build`
carry real LLM budgets ($1.50 / $9.00 ceilings). A tick that kicked both off
back-to-back would turn one bad need into a compound bill before the operator
saw a single card.

**2. Attempt cooldown, tracked separately from success.** A job is stamped
`last_success_at` only when it worked, so a failing daily job would otherwise
be retried every 15-minute tick — a money-burn loop for the spending jobs.
`last_attempt_at` is stamped on every try, and a job is not re-attempted
within `max(expected_interval_s / 4, 900)` seconds of the last attempt: a
daily job gets at most 4 tries a day, a 15-minute job backs off 15 minutes.

**3. "Nothing to do" is a success.** The research runner finding no need that
lacks a dossier means research is DONE, not stale. The job is stamped, the
same rule as the empty expiry sweep in console/tasks.py.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

import structlog

from .. import db

log = structlog.get_logger("runtime.scheduler")

# Jobs that spend LLM money; at most one runs per tick.
SPENDING = frozenset({"research.dossier", "forge.build"})


async def _stamp_success(job: str) -> None:
    await db.execute(
        "UPDATE job_registry SET last_success_at = now() WHERE job_name = $1", job)


async def _run_synthetic_sweep() -> dict:
    from ..observability.alerts import run_synthetics
    out = await run_synthetics()
    return {"fired": out["fired"], "total": out["total"]}


async def _run_artifact_sweep() -> dict:
    from ..commerce.delivery import sweep
    return await sweep()


async def _run_funnel() -> dict:
    from ..discovery.steps import run_funnel
    out = await run_funnel()
    return {"run_id": out.get("run_id")}


async def _pick_research_need() -> Optional[int]:
    """The best promoted need that has no dossier yet and no run in flight."""
    v = await db.fetchval(
        """
        SELECT n.id FROM needs n
         WHERE n.status = 'promoted'
           AND NOT EXISTS (SELECT 1 FROM dossiers d WHERE d.need_id = n.id)
         ORDER BY n.score DESC NULLS LAST, n.id
         LIMIT 1
        """)
    return int(v) if v is not None else None


async def _run_research() -> dict:
    from ..research.steps import run_research
    need_id = await _pick_research_need()
    if need_id is None:
        # Every promoted need has its dossier — the job is DONE, not stale.
        await _stamp_success("research.dossier")
        return {"nothing_to_do": True}
    out = await run_research(need_id)
    return {"need_id": need_id, "steps": {k: v["status"] for k, v in out["steps"].items()}}


async def _pick_forge_need() -> Optional[int]:
    v = await db.fetchval(
        """
        SELECT n.id FROM needs n
         WHERE n.status = 'promoted'
           AND EXISTS (SELECT 1 FROM dossiers d WHERE d.need_id = n.id)
           AND NOT EXISTS (SELECT 1 FROM artifacts a WHERE a.need_id = n.id)
         ORDER BY n.score DESC NULLS LAST, n.id
         LIMIT 1
        """)
    return int(v) if v is not None else None


async def _run_forge() -> dict:
    from ..forge.steps import run_forge
    need_id = await _pick_forge_need()
    if need_id is None:
        await _stamp_success("forge.build")
        return {"nothing_to_do": True}
    out = await run_forge(need_id)
    return {"need_id": need_id, "steps": {k: v["status"] for k, v in out["steps"].items()}}


DISPATCH: dict[str, Callable[[], Awaitable[dict]]] = {
    "alert.synthetic_sweep": _run_synthetic_sweep,
    "commerce.artifact_sweep": _run_artifact_sweep,
    "discovery.funnel": _run_funnel,
    "research.dossier": _run_research,
    "forge.build": _run_forge,
}


async def due_jobs() -> list[str]:
    """Enabled, overdue, past their attempt cooldown, and dispatchable.

    Jobs whose loops live in an app process (poll_replies, contract_test,
    harvest, expire_tasks) are deliberately absent from DISPATCH — running
    them here too would double-drive them.
    """
    rows = await db.fetch(
        """
        SELECT job_name FROM job_registry
         WHERE enabled
           AND (last_success_at IS NULL
                OR last_success_at < now() - expected_interval_s * interval '1 second')
           AND (last_attempt_at IS NULL
                OR last_attempt_at < now() -
                   GREATEST(expected_interval_s / 4, 900) * interval '1 second')
         ORDER BY job_name
        """)
    return [r["job_name"] for r in rows if r["job_name"] in DISPATCH]


async def tick() -> dict[str, Any]:
    """Run every due job once; serialised across replicas by advisory lock."""
    from . import singleton

    results: dict[str, Any] = {}
    async with singleton.hold("scheduler_tick") as owner:
        if not owner:
            return {"skipped": "another tick holds the lock"}

        # A pipeline run already in flight means the engine is busy; starting
        # another spending run beside it would race for the same needs.
        running = int(await db.fetchval(
            "SELECT count(*) FROM runs WHERE status = 'running'") or 0)

        spent = False
        for job in await due_jobs():
            if job in SPENDING and (spent or running):
                results[job] = {"deferred": "spend slot used" if spent
                                else f"{running} run(s) in flight"}
                continue
            await db.execute(
                "UPDATE job_registry SET last_attempt_at = now() WHERE job_name = $1",
                job)
            try:
                out = await DISPATCH[job]()
                results[job] = out
                if job in SPENDING and not out.get("nothing_to_do"):
                    spent = True
                log.info("scheduler.job_done", job=job, **{
                    k: v for k, v in out.items() if isinstance(v, (int, bool, str))})
            except Exception as e:                               # noqa: BLE001
                results[job] = {"error": str(e)[:300]}
                log.error("scheduler.job_failed", job=job, error=str(e)[:300])
    return results
