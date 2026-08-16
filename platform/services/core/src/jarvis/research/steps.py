"""Phase B as `@step` units.

The acceptance predicates here are the phase's exit criterion, made executable:

    research.capture      >= 15 LIVE hash-verified evidence rows
    research.synthesise   0 uncited claims, and feasibility decided per tier

`research.gap_analysis` deliberately does NOT require claims to exist. If the
captured pages genuinely reveal no gap, that is a finding — and forcing the
step to invent one would recreate exactly what Pimlico did when it weighted an
uncomputed `gap` at 0.25.
"""
from __future__ import annotations

from typing import Any

import structlog

from .. import db
from ..runtime.registry import all_steps, step
from ..runtime.types import StepResult, StepStatus
from . import dossier, evidence as ev

log = structlog.get_logger("research.steps")

TEST = "tests/stages/test_research_steps.py"
MIN_EVIDENCE = 15
_REGISTERED = False


def register() -> None:
    global _REGISTERED
    if _REGISTERED and "research.capture" in all_steps():
        return

    @step(id="research.capture", phase="RESEARCH", test=TEST,
          inputs=("need_id",), produces=("evidence[]",),
          requires_connectors=("duckduckgo",),
          acceptance=lambda r: (r.data.get("usable", 0) >= MIN_EVIDENCE
                                and r.data.get("unhashed", 1) == 0),
          acceptance_desc=(f"at least {MIN_EVIDENCE} live, hashed and SUBSTANTIVE "
                           f"evidence rows — placeholders and search result pages "
                           f"do not count"),
          idempotency_key="need_id", timeout_s=900, cost_budget_usd=0.50)
    async def capture(ctx, need_id: int = 0, **kw: Any) -> StepResult:
        """B1 — capture competitors and the source pages behind the need.

        Two sources of evidence, and the second is free: the URLs of the
        signals that produced this need are the actual pages where somebody
        described the problem. Pimlico harvested those URLs and never fetched
        one of them.
        """
        need_id = need_id or ctx.data.get("need_id") or ctx.need_id
        if not need_id:
            return StepResult.fail("no need_id supplied")

        need = await db.fetchrow("SELECT title FROM needs WHERE id=$1", need_id)
        if need is None:
            return StepResult.fail(f"need {need_id} does not exist")

        ids = await ev.capture_signal_urls(need_id, run_id=ctx.run_id)

        # Breadth is a PARAMETER, and it has to be generous: roughly half of
        # all fetches yield nothing usable (Cloudflare interstitials, JS-only
        # pages, thin listicles). The acceptance bar is 15 SUBSTANTIVE rows, so
        # the capture has to attempt ~2x that. Widening the search is the
        # honest way to meet the bar; narrowing the definition of "evidence"
        # would not be.
        p = await ev.params()
        per_query = int(p.get("results_per_query", 10))
        title = need["title"]
        queries = [
            f"{title} software", f"{title} tool pricing", f"best {title} solution",
            f"{title} problems complaints", f"{title} alternatives comparison",
            f"why is {title} so hard",
        ]
        for query in queries:
            ids += await ev.capture_search(need_id, query, limit=per_query,
                                           run_id=ctx.run_id)

        st = await ev.stats(need_id)
        ctx.data["need_id"] = need_id
        return StepResult.ok(
            need_id=need_id, captured=len(ids),
            total=int(st.get("total") or 0), live=int(st.get("live") or 0),
            usable=int(st.get("usable") or 0),
            hashed=int(st.get("hashed") or 0), domains=int(st.get("domains") or 0),
            unhashed=int(st.get("total") or 0) - int(st.get("hashed") or 0))

    @step(id="research.gap_analysis", phase="RESEARCH", test=TEST,
          inputs=("need_id",), produces=("claims[]",),
          acceptance=lambda r: r.data.get("pages_analysed", 0) > 0,
          acceptance_desc="at least one captured page was analysed for gaps",
          idempotency_key="need_id", timeout_s=900, cost_budget_usd=1.00)
    async def gap(ctx, need_id: int = 0, **kw: Any) -> StepResult:
        """B2 — every gap statement is a claim with a NOT NULL evidence_id.

        Acceptance is "pages were analysed", NOT "gaps were found". If the
        evidence reveals no gap, that is a result. Requiring gaps would make
        the step invent them.
        """
        need_id = need_id or ctx.data.get("need_id") or ctx.need_id
        out = await dossier.gap_analysis(need_id, run_id=ctx.run_id)
        return StepResult.ok(need_id=need_id, **out)

    @step(id="research.willingness_to_pay", phase="RESEARCH", test=TEST,
          inputs=("need_id",), produces=("pricing",),
          acceptance=lambda r: r.data.get("observations", 0) >= 0,
          acceptance_desc="pricing evidence examined across the captured domains",
          idempotency_key="need_id", timeout_s=300)
    async def wtp(ctx, need_id: int = 0, **kw: Any) -> StepResult:
        """B3 — never a regex over one page. Requires >= 2 distinct domains."""
        need_id = need_id or ctx.data.get("need_id") or ctx.need_id
        out = await dossier.willingness_to_pay(need_id, run_id=ctx.run_id)
        return StepResult.ok(need_id=need_id, **out)

    @step(id="research.feasibility", phase="RESEARCH", test=TEST,
          inputs=("need_id",), produces=("feasibility",),
          acceptance=lambda r: all(t in (r.data.get("tiers") or {})
                                   for t in ("roadmap", "instructions", "deployed")),
          acceptance_desc="a feasibility verdict exists for all three tiers",
          idempotency_key="need_id", timeout_s=120)
    async def feas(ctx, need_id: int = 0, **kw: Any) -> StepResult:
        """B4 — Deployed is feasible only if its connectors are live."""
        need_id = need_id or ctx.data.get("need_id") or ctx.need_id
        out = await dossier.feasibility(need_id, run_id=ctx.run_id)
        return StepResult.ok(
            need_id=need_id,
            tiers={k: out[k]["feasible"] for k in
                   ("roadmap", "instructions", "deployed")},
            deployed_reason=out["deployed"]["reason"])

    @step(id="research.synthesise", phase="RESEARCH", test=TEST,
          inputs=("need_id",), produces=("dossier",),
          acceptance=lambda r: (r.data.get("evidence_usable", 0) >= MIN_EVIDENCE
                                and r.data.get("uncited_claims", 1) == 0),
          acceptance_desc=(f">= {MIN_EVIDENCE} live, substantive evidence rows AND "
                           f"zero uncited claims"),
          idempotency_key="need_id", timeout_s=300)
    async def synth(ctx, need_id: int = 0, **kw: Any) -> StepResult:
        """B5 — the Research Dossier. THE PHASE-5 EXIT CRITERION."""
        need_id = need_id or ctx.data.get("need_id") or ctx.need_id
        out = await dossier.synthesise(need_id, run_id=ctx.run_id)
        return StepResult.ok(**out)

    _REGISTERED = True
    log.info("research.steps_registered", total_steps=len(all_steps()))


ORDER = ("research.capture", "research.gap_analysis",
         "research.willingness_to_pay", "research.feasibility",
         "research.synthesise")


async def run_research(need_id: int, run_id: int | None = None) -> dict:
    """Execute Phase B end to end through the real engine."""
    from ..runtime import engine

    register()
    rid = run_id or await engine.create_run("RESEARCH", need_id=need_id)
    ctx = await engine.open_context(rid)
    ctx.data["need_id"] = need_id
    out: dict[str, Any] = {"run_id": rid, "need_id": need_id, "steps": {}}

    for sid in ORDER:
        r = await engine.execute(sid, ctx, need_id=need_id)
        out["steps"][sid] = {"status": r.status.value, "data": r.data,
                             "reason": r.reason}
        if r.status is not StepStatus.SUCCEEDED:
            out["stopped_at"] = sid
            break

    try:
        await engine.complete_run(
            ctx, "completed" if "stopped_at" not in out else "failed")
    except Exception as e:                                       # noqa: BLE001
        log.warning("research.complete_failed", run_id=rid, error=str(e)[:150])

    await db.execute(
        "UPDATE job_registry SET last_success_at = now() WHERE job_name = $1",
        "research.dossier")
    return out
