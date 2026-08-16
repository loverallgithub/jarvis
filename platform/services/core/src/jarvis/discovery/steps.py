"""Phase A as `@step` units — the first real steps in the registry.

Until now `jpd steps` has been empty by design: the engine shipped before the
steps. This is where the contract starts being enforced on real work.

Every step below declares an **acceptance predicate**, and the engine — not the
step — decides whether it succeeded. Three of these predicates are the whole
reason the funnel can be trusted:

  · `normalise` fails on an empty window. A funnel that reports success on zero
    input is exactly how Pimlico ran for three weeks with nobody noticing.
  · `gate` succeeds when the census was WRITTEN, not when a cluster passed.
    Evaluating and rejecting everything is a correct outcome; failing to record
    why is not.
  · `promote` succeeds when a decision was reached and recorded — promoting
    nothing is a legitimate result, provided the reason is in the census.

Registration is idempotent and explicit (`register()`), so importing this
module has no side effects and the tests can control the registry.
"""
from __future__ import annotations

from typing import Any

import structlog

from .. import db
from ..runtime.registry import all_steps, step
from ..runtime.types import StepResult, StepStatus
from . import cluster as cl
from . import funnel, gates

log = structlog.get_logger("discovery.steps")

TEST = "tests/stages/test_discovery_steps.py"
_REGISTERED = False


def register() -> None:
    """Register Phase A. Idempotent — safe to call from startup and tests."""
    global _REGISTERED
    if _REGISTERED and "discovery.normalise" in all_steps():
        return

    @step(id="discovery.normalise", phase="DISCOVER", test=TEST,
          inputs=(), produces=("docs",),
          acceptance=lambda r: r.data.get("admitted", 0) > 0,
          acceptance_desc="at least one admissible signal in the 30-day window",
          idempotency_key=None, timeout_s=120)
    async def normalise(ctx, **kw: Any) -> StepResult:
        """A2 — dedupe, apply the admission rule, take the rolling window."""
        docs = await cl.load_window()
        ctx.data["docs"] = docs
        if not docs:
            return StepResult.fail(
                "no admissible signals in the window — either nothing was "
                "harvested or every concept failed the 4-content-word rule")
        return StepResult.ok(admitted=len(docs),
                             source_types=sorted({d.source_type for d in docs}))

    @step(id="discovery.cluster", phase="DISCOVER", test=TEST,
          inputs=("docs",), produces=("clusters",),
          acceptance=lambda r: r.data.get("clusters", 0) > 0,
          acceptance_desc="at least one cluster of >= 2 signals",
          timeout_s=600)
    async def cluster_step(ctx, **kw: Any) -> StepResult:
        """A3 — cluster, OFF THE EVENT LOOP.

        Pimlico ran this inline: 181s for 1,690 signals, HTTP frozen, and the
        run outlived its own lease.
        """
        docs = ctx.data.get("docs") or await cl.load_window()
        clusters = await cl.cluster(docs)
        if not clusters:
            return StepResult.fail(
                f"{len(docs)} signals produced no cluster of >= {cl.MIN_CLUSTER_SIZE} "
                f"— nothing corroborates anything else")
        ids = await cl.persist(clusters, run_id=ctx.run_id)
        ctx.data["clusters"] = list(zip(ids, clusters))
        return StepResult.ok(
            clusters=len(clusters), largest=max(c.size for c in clusters),
            cross_source=sum(1 for c in clusters if len(c.source_types) >= 2))

    @step(id="discovery.gate", phase="DISCOVER", test=TEST,
          inputs=("clusters",), produces=("verdicts",),
          acceptance=lambda r: r.data.get("evaluated", 0) > 0,
          acceptance_desc="every cluster evaluated and its census persisted",
          timeout_s=300)
    async def gate_step(ctx, **kw: Any) -> StepResult:
        """A4 — evaluate every gate and persist the census.

        🔴 Acceptance is "the census was written", NOT "something passed".
        Rejecting every cluster is a correct outcome; being unable to say why
        is the failure. Pimlico's census lived in process memory and was lost
        on every restart, so three weeks of zero promotions were undiagnosable.
        """
        pairs = ctx.data.get("clusters") or []
        verdicts = []
        for cid, c in pairs:
            v = await gates.evaluate(c, cluster_id=cid, run_id=ctx.run_id)
            verdicts.append((cid, c, v))
        ctx.data["verdicts"] = verdicts
        passing = [c for _, _, v in verdicts for c in [v] if v.passed]
        return StepResult.ok(evaluated=len(verdicts), passing=len(passing),
                             blocked_by=await gates.blocking_gate())

    @step(id="discovery.qualify", phase="DISCOVER", test=TEST,
          inputs=("verdicts",), produces=("qualified",),
          acceptance=lambda r: r.data.get("considered", 0) >= 0,
          acceptance_desc="qualification attempted for every gate-passing cluster",
          timeout_s=300)
    async def qualify_step(ctx, **kw: Any) -> StepResult:
        """A5 — who holds this pain, and can they pay?

        Reads named voices instead of inferring an audience. A need that cannot
        be qualified is PARKED, not built.
        """
        verdicts = ctx.data.get("verdicts") or []
        out = []
        for cid, c, v in verdicts:
            if not v.passed:
                continue
            q = await funnel.qualify(cid)
            out.append((cid, c, v, q))
        ctx.data["qualified"] = out
        return StepResult.ok(considered=len(out),
                             qualified=sum(1 for *_, q in out if q.qualified))

    @step(id="discovery.score", phase="DISCOVER", test=TEST,
          inputs=("qualified",), produces=("scored",),
          acceptance=lambda r: all(g is None for g in r.data.get("gaps", [])),
          acceptance_desc="gap is NULL for every scored need — it is deferred to Phase B",
          timeout_s=120)
    async def score_step(ctx, **kw: Any) -> StepResult:
        """A6 — weighted sub-scores.

        The acceptance predicate asserts `gap is None`. Pimlico weighted `gap`
        at 0.25 with no competitive data at all; making that unrepresentable is
        worth a predicate of its own.
        """
        qualified = ctx.data.get("qualified") or []
        scored = []
        for cid, c, v, q in qualified:
            sc = await funnel.score(c, v)
            scored.append((cid, c, v, q, sc))
        ctx.data["scored"] = scored
        return StepResult.ok(scored=len(scored), gaps=[s.gap for *_, s in scored],
                             top=max((s.total for *_, s in scored), default=0))

    @step(id="discovery.promote", phase="DISCOVER", test=TEST,
          inputs=("scored",), produces=("needs",),
          acceptance=lambda r: r.data.get("decided", 0) >= 0,
          acceptance_desc="a promotion decision was reached and recorded for every candidate",
          timeout_s=300)
    async def promote_step(ctx, **kw: Any) -> StepResult:
        """A7 — promote, or record precisely why not.

        Promoting nothing is a legitimate outcome. Promoting nothing *and being
        unable to say why* is the Pimlico failure, and the census makes it
        impossible here.
        """
        scored = ctx.data.get("scored") or []
        promoted = []
        for cid, c, v, q, sc in scored:
            need_id = await funnel.promote(c, cid, v, q, sc, run_id=ctx.run_id)
            if need_id:
                promoted.append(need_id)
                await funnel.announce(need_id, v, c)
        ctx.data["needs"] = promoted
        return StepResult.ok(decided=len(scored), promoted=len(promoted),
                             need_ids=promoted)

    _REGISTERED = True
    log.info("discovery.steps_registered", steps=len(all_steps()))


ORDER = ("discovery.normalise", "discovery.cluster", "discovery.gate",
         "discovery.qualify", "discovery.score", "discovery.promote")


async def run_funnel(run_id: int | None = None) -> dict:
    """Execute Phase A end to end through the real engine.

    Stops at the first non-success. A funnel that carries on past a failed step
    produces a result nobody can interpret — which is the state Pimlico's
    pipeline lived in.
    """
    from ..runtime import engine

    register()
    rid = run_id or await engine.create_run("DISCOVER")
    ctx = await engine.open_context(rid)
    out: dict[str, Any] = {"run_id": rid, "steps": {}}

    for sid in ORDER:
        r = await engine.execute(sid, ctx)
        out["steps"][sid] = {"status": r.status.value, "data": r.data,
                             "reason": r.reason}
        if r.status is not StepStatus.SUCCEEDED:
            out["stopped_at"] = sid
            break

    out["promoted"] = out["steps"].get("discovery.promote", {}).get(
        "data", {}).get("need_ids", [])

    # 🔴 COMPLETE THE RUN. Without this every funnel execution left a run at
    # status='running' with an expiring lease, and `jpd doctor` correctly
    # reported "5 orphaned runs" — a run that never ends is indistinguishable
    # from one that died mid-step, which is exactly the ambiguity the status
    # column exists to remove.
    try:
        await engine.complete_run(
            ctx, "completed" if "stopped_at" not in out else "failed")
    except Exception as e:                                       # noqa: BLE001
        log.warning("funnel.complete_failed", run_id=rid, error=str(e)[:150])

    await db.execute(
        "UPDATE job_registry SET last_success_at = now() WHERE job_name = $1",
        "discovery.funnel")
    return out
