"""Recovery checkpoints — the machine-readable half of resume.

Pimlico's checkpoint is a 3,798-line markdown file. It is genuinely excellent
institutional memory and genuinely unusable as a resume mechanism: a machine
cannot act on it, and a human must read all of it to find the one line that
matters.

JPD splits the two concerns. The `checkpoints` table is what `jpd resume`
reads. `CHECKPOINT.md` is GENERATED from it, plus a hand-written "why" section
that survives regeneration.

────────────────────────────────────────────────────────────────────────────
THE RESUME RULE — earned from Pimlico incident [T-1.12]
────────────────────────────────────────────────────────────────────────────
On resume, **re-run the last step's acceptance predicate before assuming
anything.** A missing DONE line is not evidence of missing work: a session
that died *after* doing the work leaves exactly the same trace as one that
died *before*. In [T-1.12] a ledger line read IN-PROGRESS while the work was
in fact complete, and re-doing it was the expensive mistake.

`verify_last()` below is that rule, executable.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .. import db
from .registry import get as get_spec, StepDefinitionError
from .types import StepResult

# Checkpoints are written at these two moments, because they are the two
# places a session actually dies.
ON_PHASE_BOUNDARY = "phase_boundary"
ON_HUMAN_TASK = "before_human_task"
EXPLICIT = "explicit"


async def write(*, phase: str, label: str, reason: str,
                run_id: Optional[int] = None,
                state: Optional[dict[str, Any]] = None,
                resumable_from: Optional[str] = None) -> int:
    """Write a checkpoint. `reason` is mandatory and is not decoration —
    a checkpoint with no stated reason cannot be judged on resume."""
    if not reason.strip():
        raise ValueError("a checkpoint must state why it was written")
    return int(await db.fetchval(
        """
        INSERT INTO checkpoints (run_id, phase, label, reason, state_json, resumable_from)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6)
        RETURNING id
        """,
        run_id, phase, label, reason, json.dumps(state or {}, default=str),
        resumable_from))


async def latest(run_id: Optional[int] = None) -> Optional[dict]:
    if run_id is None:
        row = await db.fetchrow(
            "SELECT * FROM checkpoints ORDER BY id DESC LIMIT 1")
    else:
        row = await db.fetchrow(
            "SELECT * FROM checkpoints WHERE run_id = $1 ORDER BY id DESC LIMIT 1", run_id)
    return dict(row) if row else None


async def history(run_id: Optional[int] = None, limit: int = 20) -> list[dict]:
    if run_id is None:
        rows = await db.fetch(
            "SELECT * FROM checkpoints ORDER BY id DESC LIMIT $1", limit)
    else:
        rows = await db.fetch(
            "SELECT * FROM checkpoints WHERE run_id = $1 ORDER BY id DESC LIMIT $2",
            run_id, limit)
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------

async def resume_report(run_id: Optional[int] = None) -> dict[str, Any]:
    """Everything needed to decide what to do next. Works on an EMPTY DB.

    An empty database is a legitimate state, not an error — it is the state
    every build starts in, and `jpd resume` must be runnable before there is
    anything to resume. It returns `empty: True` and exits 0.
    """
    where = "" if run_id is None else " WHERE r.id = $1"
    args: tuple = () if run_id is None else (run_id,)

    runs = await db.fetch(
        f"""
        SELECT r.id, r.phase, r.status, r.need_id, r.solution_id, r.cost_usd,
               r.lease_owner, r.lease_expires_at, r.kill_requested,
               r.started_at, r.ended_at
          FROM runs r{where}
         ORDER BY r.id DESC LIMIT 25
        """, *args)

    open_tasks = await db.fetch(
        """
        SELECT ref, title, why, verify_command, expires_at, status,
               (expires_at < now()) AS expired
          FROM human_tasks WHERE status = 'open' ORDER BY created_at
        """)

    unhealthy = await db.fetch(
        "SELECT connector, state, fail_streak, zero_yield_streak FROM connector_health "
        "WHERE state <> 'live' ORDER BY connector")

    cp = await latest(run_id)

    out: dict[str, Any] = {
        "empty": len(runs) == 0,
        "checkpoint": cp,
        "runs": [dict(r) for r in runs],
        "open_human_tasks": [dict(t) for t in open_tasks],
        "non_live_connectors": [dict(c) for c in unhealthy],
        "resumable": [],
    }

    for r in runs:
        if r["status"] in ("running", "paused", "blocked_on_human"):
            step = await db.fetchrow(
                "SELECT id, step_id, status, accepted, acceptance_reason, attempt, "
                "repair_count, ended_at FROM steps WHERE run_id = $1 ORDER BY id DESC LIMIT 1",
                r["id"])
            out["resumable"].append({
                "run_id": r["id"], "phase": r["phase"], "status": r["status"],
                "last_step": dict(step) if step else None,
                "lease_held_by": r["lease_owner"],
                # A run whose lease has expired is safe to take over. One that
                # still holds it is being worked by someone else RIGHT NOW.
                "lease_expired": (r["lease_expires_at"] is None
                                  or r["lease_expires_at"].timestamp() < __import__("time").time()),
            })

    return out


async def verify_last(run_id: Optional[int] = None) -> dict[str, Any]:
    """THE RESUME RULE, executable.

    Re-runs the last step's acceptance predicate against its PERSISTED result.
    Three outcomes, and the third is the one that matters:

      - agrees       : the stored verdict still holds. Trust it.
      - disagrees    : the stored verdict is wrong NOW. Something changed
                       underneath (a URL died, a file vanished). Do not trust
                       the DONE line.
      - unverifiable : the step is not registered in this build, or its
                       predicate cannot run from stored state. Say so rather
                       than assuming success.
    """
    if run_id is None:
        row = await db.fetchrow(
            "SELECT * FROM steps ORDER BY id DESC LIMIT 1")
    else:
        row = await db.fetchrow(
            "SELECT * FROM steps WHERE run_id = $1 ORDER BY id DESC LIMIT 1", run_id)

    if row is None:
        return {"verdict": "no_steps",
                "detail": "nothing has run yet — there is nothing to re-verify"}

    stored_status = row["status"]
    stored_accepted = row["accepted"]

    try:
        spec = get_spec(row["step_id"])
    except StepDefinitionError:
        return {"verdict": "unverifiable", "step_id": row["step_id"],
                "stored_status": stored_status,
                "detail": f"step {row['step_id']!r} is not registered in this build; "
                          f"its predicate cannot be re-run here"}

    result = StepResult.rehydrate(row["result_json"], row["evidence_json"])
    now_accepted, reason = spec.evaluate_acceptance(result)

    agrees = (bool(stored_accepted) == bool(now_accepted)) if stored_accepted is not None \
        else (stored_status != "succeeded" and not now_accepted)

    return {
        "verdict": "agrees" if agrees else "disagrees",
        "step_id": row["step_id"],
        "run_id": row["run_id"],
        "stored_status": stored_status,
        "stored_accepted": stored_accepted,
        "reevaluated_accepted": now_accepted,
        "reason": reason,
        "detail": ("stored verdict still holds" if agrees else
                   "STORED VERDICT NO LONGER HOLDS — do not trust the recorded status; "
                   "the world changed under this step"),
    }


# ---------------------------------------------------------------------------
# CHECKPOINT.md generation
# ---------------------------------------------------------------------------

WHY_MARKER = "<!-- HAND-WRITTEN BELOW — SURVIVES REGENERATION -->"


def render_markdown(report: dict[str, Any], why_section: str = "") -> str:
    """Generate the human-facing checkpoint from the machine state.

    Everything above WHY_MARKER is regenerated every time. Everything below it
    is hand-written and preserved — that is where the reasoning lives, and
    reasoning is the one thing a table cannot hold.
    """
    L: list[str] = []
    A = L.append
    A("# JPD CHECKPOINT — generated")
    A("")
    A("> Generated from the `checkpoints` table by `jpd checkpoint render`.")
    A("> **Resume rule:** re-run the last step's acceptance predicate before assuming")
    A("> anything. A missing DONE line is not evidence of missing work.")
    A("")

    cp = report.get("checkpoint")
    if cp:
        A(f"**Latest checkpoint:** `{cp['label']}` — {cp['reason']}  ")
        A(f"Phase `{cp['phase']}` · written {cp['created_at']:%Y-%m-%d %H:%M UTC}"
          + (f" · resumable from `{cp['resumable_from']}`" if cp.get("resumable_from") else ""))
    else:
        A("**Latest checkpoint:** none — no checkpoint has ever been written.")
    A("")

    if report.get("empty"):
        A("## State: EMPTY")
        A("")
        A("No runs exist. This is a legitimate state, not an error — it is where every")
        A("build starts. `jpd resume` is expected to work here and exit 0.")
    else:
        A("## Resumable runs")
        A("")
        if not report["resumable"]:
            A("None. Every run has reached a terminal state.")
        else:
            A("| run | phase | status | last step | last status | lease |")
            A("|---|---|---|---|---|---|")
            for r in report["resumable"]:
                ls = r.get("last_step") or {}
                lease = "free" if r["lease_expired"] else f"HELD by {r['lease_held_by']}"
                A(f"| {r['run_id']} | {r['phase']} | {r['status']} | "
                  f"`{ls.get('step_id', '—')}` | {ls.get('status', '—')} | {lease} |")
    A("")

    tasks = report.get("open_human_tasks") or []
    A(f"## Open human tasks — {len(tasks)}")
    A("")
    if not tasks:
        A("None open.")
    else:
        for t in tasks:
            flag = " ⏰ **EXPIRED**" if t.get("expired") else ""
            A(f"- **{t['ref']}** — {t['title']}{flag}  ")
            A(f"  *Why:* {t['why']}  ")
            if t.get("verify_command"):
                A(f"  *Verify:* `{t['verify_command']}`")
    A("")

    nl = report.get("non_live_connectors") or []
    A(f"## Connectors not live — {len(nl)}")
    A("")
    if not nl:
        A("All connectors live.")
    else:
        A("| connector | state | fail streak | zero-yield streak |")
        A("|---|---|---|---|")
        for c in nl:
            A(f"| {c['connector']} | {c['state']} | {c['fail_streak']} | {c['zero_yield_streak']} |")
    A("")
    A(WHY_MARKER)
    A("")
    A(why_section.strip() or "## Why\n\n_(hand-written; add reasoning here — it survives regeneration)_")
    A("")
    return "\n".join(L)


def split_why(existing: str) -> str:
    """Recover the hand-written half of an existing CHECKPOINT.md."""
    if WHY_MARKER in existing:
        return existing.split(WHY_MARKER, 1)[1]
    return ""
