"""The step engine.

Everything in JPD is a Step, and this module is the only thing allowed to
decide what a step's status becomes. Step functions *propose*; the engine
*disposes*. That separation is what makes the acceptance predicate binding
rather than advisory.

The six non-negotiable rules (02-ARCHITECTURE §2), and where each is enforced:

1. no null status ............... types.StepStatus + the DB CHECK
2. succeeded requires acceptance  _finalise(), via spec.evaluate_acceptance
3. dormant connector => skip ..... _connector_gate()
4. lease-guarded mutation ........ every UPDATE carries `AND lease_owner = $1`
5. cost budget ................... RunContext.spend() and the post-hoc check
6. no test => no registration .... registry.step()
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .. import db
from ..config import settings
from . import lease as lease_mod
from .registry import StepSpec, get as get_spec
from .types import StepResult, StepStatus

import structlog

log = structlog.get_logger("runtime.engine")


class BudgetExceeded(RuntimeError):
    """A step tried to spend past its declared budget.

    Fails the step, not the invoice. Pimlico had no per-step budget at all and
    a repair loop that could never terminate ran at full LLM cost.
    """


@dataclass
class RunContext:
    run_id: int
    phase: str
    owner: str
    need_id: Optional[int] = None
    solution_id: Optional[int] = None
    data: dict[str, Any] = field(default_factory=dict)
    _spent: float = 0.0
    _budget: float = 0.0

    def spend(self, usd: float) -> None:
        """Record spend and fail fast if it breaches the step's budget."""
        self._spent += float(usd)
        if self._budget > 0 and self._spent > self._budget:
            raise BudgetExceeded(
                f"spent ${self._spent:.4f} against a budget of ${self._budget:.4f}")

    @property
    def spent(self) -> float:
        return self._spent

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------

async def create_run(phase: str, need_id: Optional[int] = None,
                     solution_id: Optional[int] = None) -> int:
    return int(await db.fetchval(
        "INSERT INTO runs (need_id, solution_id, phase, status) "
        "VALUES ($1, $2, $3, 'running') RETURNING id",
        need_id, solution_id, phase))


async def open_context(run_id: int, owner: Optional[str] = None) -> RunContext:
    owner = owner or lease_mod.new_owner()
    row = await db.fetchrow("SELECT * FROM runs WHERE id = $1", run_id)
    if row is None:
        raise LookupError(f"run {run_id} does not exist")
    if not await lease_mod.acquire(run_id, owner):
        raise lease_mod.LeaseLost(
            f"run {run_id} is leased by {row['lease_owner']!r} "
            f"or has been killed (kill_requested={row['kill_requested']})")
    return RunContext(run_id=run_id, phase=row["phase"], owner=owner,
                      need_id=row["need_id"], solution_id=row["solution_id"])


# ---------------------------------------------------------------------------
# connector gate — C3
# ---------------------------------------------------------------------------

async def _connector_gate(spec: StepSpec) -> Optional[str]:
    """Return the name of the first non-live required connector, else None.

    A step whose connectors are not live returns `skipped_dormant`. It does not
    run, does not fabricate, and does not persist partial output. This single
    check kills the entire Sintra class of failure: the connector was broken,
    the code carried on, and the failure text was published to LinkedIn.
    """
    wanted = list(spec.requires_connectors) + list(spec.requires_any_connectors)
    if not wanted:
        return None
    rows = await db.fetch(
        "SELECT connector, state FROM connector_health WHERE connector = ANY($1::text[])",
        wanted)
    states = {r["connector"]: r["state"] for r in rows}
    for name in spec.requires_connectors:
        # An unregistered connector is treated as dormant, never as live.
        # 'Absent' must be the safe direction.
        if states.get(name, "dormant") != "live":
            return name

    # ANY-of: satisfied by one live route. Used where a step has a genuine
    # fallback — the forge can reach its model through Anthropic OR OpenRouter,
    # so requiring BOTH would strand it on a provider outage, and requiring
    # NEITHER would let it run with no working provider at all. Absent is still
    # the safe direction: an empty or all-dormant set fails.
    if spec.requires_any_connectors:
        if not any(states.get(n, "dormant") == "live"
                   for n in spec.requires_any_connectors):
            return " or ".join(spec.requires_any_connectors)
    return None


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------

async def execute(step_id: str, ctx: RunContext, **kwargs: Any) -> StepResult:
    spec = get_spec(step_id)
    idem = _idempotency_value(spec, ctx, kwargs)

    # -- 0. we must still hold the lease before we do anything at all -------
    if not await lease_mod.held_by(ctx.run_id, ctx.owner):
        raise lease_mod.LeaseLost(
            f"run {ctx.run_id}: lease not held by {ctx.owner} at entry to {step_id}")

    # -- 1. idempotency ----------------------------------------------------
    cached = await db.fetchrow(
        "SELECT * FROM steps WHERE run_id = $1 AND step_id = $2 "
        "AND idempotency_value = $3 AND status = 'succeeded'",
        ctx.run_id, step_id, idem)
    if cached is not None:
        return StepResult.rehydrate(cached["result_json"], cached["evidence_json"])

    # -- 2. connector gate -------------------------------------------------
    dormant = await _connector_gate(spec)
    if dormant is not None:
        result = StepResult.dormant(dormant)
        await _persist(spec, ctx, result, idem, accepted=None,
                       reason=f"required connector {dormant!r} is not live", elapsed=0.0)
        return result

    # -- 3. run ------------------------------------------------------------
    ctx._budget = spec.cost_budget_usd
    ctx._spent = 0.0
    row_id = await _begin(spec, ctx, idem)
    started = time.monotonic()

    # 🔴 HEARTBEAT THE LEASE WHILE THE STEP RUNS.
    #
    # Without this a long step outlives its own lease and the NEXT step raises
    # LeaseLost. Observed: `forge.generate` spent 580s on LLM calls against a
    # 120s TTL, completed successfully, and the run then died on the following
    # step — after paying for every token.
    #
    # This is the exact failure this codebase quotes Pimlico for ("clustering
    # took 181s inline ... the run outlived its lease"), reproduced here. A
    # lease that only refreshes between steps assumes steps are short, and the
    # whole point of `timeout_s` is that some are not.
    heartbeat = asyncio.create_task(_renew_lease(ctx))

    try:
        result = await asyncio.wait_for(spec.fn(ctx, **kwargs), timeout=spec.timeout_s)
        if not isinstance(result, StepResult):
            # A step returning a bare value is a step that cannot be gated.
            # Refuse it loudly rather than coercing it into a success.
            result = StepResult.fail(
                f"step returned {type(result).__name__}, expected StepResult")
    except asyncio.TimeoutError:
        result = StepResult.fail(f"timed out after {spec.timeout_s}s")
    except BudgetExceeded as e:
        result = StepResult.fail(f"cost budget exceeded: {e}")
    except lease_mod.LeaseLost:
        raise
    except Exception as e:                                      # noqa: BLE001
        result = StepResult.fail(f"{type(e).__name__}: {e}")
    finally:
        heartbeat.cancel()

    elapsed = time.monotonic() - started
    if result.cost_usd == 0.0 and ctx.spent > 0:
        result.cost_usd = ctx.spent

    # -- 4. post-hoc budget check -----------------------------------------
    if spec.cost_budget_usd > 0 and result.cost_usd > spec.cost_budget_usd:
        result = StepResult.fail(
            f"cost ${result.cost_usd:.4f} exceeds budget ${spec.cost_budget_usd:.4f}",
            **result.data)

    # -- 5. acceptance -----------------------------------------------------
    return await _finalise(spec, ctx, result, idem, row_id, elapsed)


async def _renew_lease(ctx: RunContext) -> None:
    """Refresh the run lease at a third of its TTL until cancelled.

    Stops renewing the moment renewal FAILS — a lost lease must not be
    papered over by a heartbeat that keeps trying. If the run was killed, the
    step's own guarded write will discover it and raise.
    """
    interval = max(5, settings.lease_ttl_s // 3)
    try:
        while True:
            await asyncio.sleep(interval)
            if not await lease_mod.renew(ctx.run_id, ctx.owner):
                log.warning("engine.lease_renew_failed", run_id=ctx.run_id,
                            owner=ctx.owner)
                return
    except asyncio.CancelledError:
        raise
    except Exception as e:                                       # noqa: BLE001
        log.warning("engine.heartbeat_error", run_id=ctx.run_id, error=str(e)[:150])


async def _finalise(spec: StepSpec, ctx: RunContext, result: StepResult,
                    idem: str, row_id: int, elapsed: float) -> StepResult:
    """Apply the acceptance predicate and write the outcome.

    RULE 2 lives in the four lines below: a proposed SUCCEEDED that fails its
    predicate becomes FAILED, and the evidence is RETAINED for diagnosis
    rather than discarded. A step never marks itself successful.
    """
    accepted: Optional[bool] = None
    reason = result.reason

    if result.status is StepStatus.SUCCEEDED:
        accepted, reason = spec.evaluate_acceptance(result)
        if not accepted:
            result = StepResult(
                status=StepStatus.FAILED, data=result.data, evidence=result.evidence,
                cost_usd=result.cost_usd, reason=reason)

    await _update(spec, ctx, result, idem, row_id, accepted, reason, elapsed)
    return result


def _idempotency_value(spec: StepSpec, ctx: RunContext, kwargs: dict) -> str:
    if not spec.idempotency_key:
        return ""
    key = spec.idempotency_key
    if key in kwargs:
        return str(kwargs[key])
    if key in ctx.data:
        return str(ctx.data[key])
    for attr in ("need_id", "solution_id", "run_id"):
        if key == attr:
            return str(getattr(ctx, attr))
    return ""


async def _begin(spec: StepSpec, ctx: RunContext, idem: str) -> int:
    """Insert the running row, carrying forward the DURABLE repair_count.

    Pimlico's repair guard tested `attempts`, which advance() reset to 0 on
    every transition — so the guard was always true and a section the model
    reliably answered 'TBD' looped forever at full LLM cost. repair_count is
    carried across attempts here, on purpose, and is never reset.
    """
    prior = await db.fetchrow(
        "SELECT max(attempt) AS a, max(repair_count) AS r FROM steps "
        "WHERE run_id = $1 AND step_id = $2 AND idempotency_value = $3",
        ctx.run_id, spec.id, idem)
    attempt = int((prior["a"] or 0)) + 1 if prior else 1
    repairs = int(prior["r"] or 0) if prior else 0

    return int(await db.fetchval(
        """
        INSERT INTO steps (run_id, step_id, phase, status, attempt, repair_count,
                           idempotency_value, lease_owner)
        VALUES ($1, $2, $3, 'running', $4, $5, $6, $7)
        RETURNING id
        """,
        ctx.run_id, spec.id, spec.phase, attempt, repairs, idem, ctx.owner))


async def _update(spec: StepSpec, ctx: RunContext, result: StepResult, idem: str,
                  row_id: int, accepted: Optional[bool], reason: Optional[str],
                  elapsed: float) -> None:
    """RULE 4. The guard Pimlico was missing.

    ⚠️ The EXISTS clause is load-bearing and was added after a test caught its
    absence. Guarding on `steps.lease_owner` alone is NOT enough: that column
    is written by this very step at _begin(), so it always matches us and the
    guard silently passes. The authority is the RUN's lease — the thing `kill`
    actually clears. Check ownership where it can be taken away from you, not
    where you wrote it yourself.

    If the run was killed mid-step, zero rows update and we raise instead of
    writing. That is what stops a killed run resurrecting with a success.
    """
    updated = await db.fetchval(
        """
        UPDATE steps
           SET status = $3, accepted = $4, acceptance_reason = $5,
               result_json = $6::jsonb, evidence_json = $7::jsonb,
               cost_usd = $8, ended_at = now()
         WHERE id = $1 AND lease_owner = $2
           AND EXISTS (SELECT 1 FROM runs r
                        WHERE r.id = steps.run_id
                          AND r.lease_owner = $2
                          AND r.kill_requested = FALSE)
        RETURNING id
        """,
        row_id, ctx.owner, result.status.value, accepted, reason,
        result.to_json(), result.evidence_json(), result.cost_usd)

    if updated is None:
        raise lease_mod.LeaseLost(
            f"run {ctx.run_id}: lease lost during {spec.id}; refusing to write a "
            f"{result.status.value} outcome for a run we no longer own")

    await db.execute(
        "UPDATE runs SET cost_usd = cost_usd + $2 WHERE id = $1 AND lease_owner = $3",
        ctx.run_id, result.cost_usd, ctx.owner)

    # The RUN's status must reflect that it is waiting on a person. Without
    # this the run reads as `running` forever while nothing is happening, and
    # "blocked on a human" becomes indistinguishable from "stuck" — which is
    # exactly how a Pimlico approval sat unnoticed for five days.
    if result.status is StepStatus.BLOCKED_ON_HUMAN:
        await db.execute(
            "UPDATE runs SET status='blocked_on_human' WHERE id=$1 AND lease_owner=$2 "
            "AND status='running'", ctx.run_id, ctx.owner)
    elif result.status is StepStatus.SUCCEEDED:
        # Unblock on the way back out, so a resumed run stops advertising a
        # block that has been answered.
        await db.execute(
            "UPDATE runs SET status='running' WHERE id=$1 AND lease_owner=$2 "
            "AND status='blocked_on_human'", ctx.run_id, ctx.owner)


async def _persist(spec: StepSpec, ctx: RunContext, result: StepResult, idem: str,
                   accepted: Optional[bool], reason: str, elapsed: float) -> None:
    """Single-shot insert for outcomes that never entered `running`."""
    inserted = await db.fetchval(
        """
        INSERT INTO steps (run_id, step_id, phase, status, accepted, acceptance_reason,
                           idempotency_value, result_json, evidence_json, lease_owner, ended_at)
        SELECT $1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10, now()
         WHERE EXISTS (SELECT 1 FROM runs WHERE id = $1 AND lease_owner = $10
                         AND kill_requested = FALSE)
        RETURNING id
        """,
        ctx.run_id, spec.id, spec.phase, result.status.value, accepted, reason,
        idem, result.to_json(), result.evidence_json(), ctx.owner)
    if inserted is None:
        raise lease_mod.LeaseLost(f"run {ctx.run_id}: lease lost before persisting {spec.id}")


# ---------------------------------------------------------------------------
# repair — a distinct step, not a retry
# ---------------------------------------------------------------------------

async def repair_budget_remaining(run_id: int, step_id: str) -> int:
    """How many repairs this step may still attempt.

    Reads the DURABLE counter. Returns 0 when exhausted, and 0 means stop —
    the caller must not reinterpret it.
    """
    spec = get_spec(step_id)
    if not spec.repairable:
        return 0
    used = await db.fetchval(
        "SELECT coalesce(max(repair_count), 0) FROM steps WHERE run_id = $1 AND step_id = $2",
        run_id, step_id)
    return max(0, spec.max_repairs - int(used or 0))


async def record_repair(run_id: int, step_id: str, owner: str) -> int:
    """Increment the durable repair counter. Returns the new count."""
    new = await db.fetchval(
        """
        UPDATE steps SET repair_count = repair_count + 1
         WHERE id = (SELECT id FROM steps WHERE run_id = $1 AND step_id = $2
                      ORDER BY id DESC LIMIT 1)
           AND lease_owner = $3
           AND EXISTS (SELECT 1 FROM runs r WHERE r.id = steps.run_id
                         AND r.lease_owner = $3 AND r.kill_requested = FALSE)
        RETURNING repair_count
        """,
        run_id, step_id, owner)
    if new is None:
        raise lease_mod.LeaseLost(f"run {run_id}: cannot record repair for {step_id}")
    return int(new)


# ---------------------------------------------------------------------------
# run completion
# ---------------------------------------------------------------------------

async def complete_run(ctx: RunContext, status: str = "completed") -> None:
    ok = await db.fetchval(
        "UPDATE runs SET status = $3, ended_at = now(), lease_owner = NULL "
        "WHERE id = $1 AND lease_owner = $2 RETURNING id",
        ctx.run_id, ctx.owner, status)
    if ok is None:
        raise lease_mod.LeaseLost(f"run {ctx.run_id}: cannot complete a run we do not own")


async def last_step(run_id: int) -> Optional[dict]:
    row = await db.fetchrow(
        "SELECT * FROM steps WHERE run_id = $1 ORDER BY id DESC LIMIT 1", run_id)
    return dict(row) if row else None
