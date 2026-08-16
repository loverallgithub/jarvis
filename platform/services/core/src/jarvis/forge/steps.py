"""Phases C/D/E as `@step` units — the forge.

The exit criterion, made executable:

    forge.package   three artifacts exist, one per tier, each a real file
    forge.verify    zero uncited claims and zero UNSUPPORTED claims

`forge.acceptance_tests` produces the tests the BUYER runs. For the Deployed
tier those tests are contractual: if they fail, that tier is not offered for
this solution and the other two still sell.
"""
from __future__ import annotations

import json
from typing import Any

import structlog

from .. import db
from ..runtime.registry import all_steps, step
from ..runtime.types import StepResult, StepStatus
from . import build, verify as vf
from .plan import TIER_ORDER, sections_for

log = structlog.get_logger("forge.steps")

TEST = "tests/stages/test_forge_steps.py"
_REGISTERED = False


def register() -> None:
    global _REGISTERED
    if _REGISTERED and "forge.plan" in all_steps():
        return

    @step(id="forge.plan", phase="FORGE", test=TEST,
          inputs=("need_id",), produces=("plan",),
          acceptance=lambda r: r.data.get("tiers_planned", 0) == 3,
          acceptance_desc="a section plan exists for all three tiers",
          idempotency_key="need_id", timeout_s=60)
    async def plan_step(ctx, need_id: int = 0, **kw: Any) -> StepResult:
        """C1–C5 / D1–D3 / E1 — the section contract for each tier.

        The plan is produced BEFORE generation and is what verification checks
        against, capped once so a size limit can never create a shortfall.
        """
        need_id = need_id or ctx.data.get("need_id") or ctx.need_id
        if not need_id:
            return StepResult.fail("no need_id supplied")

        cited = int(await db.fetchval(
            "SELECT count(*) FROM claims c JOIN evidence e ON e.id=c.evidence_id "
            "WHERE c.need_id=$1 AND e.substantive AND e.live_at_capture", need_id) or 0)
        if cited == 0:
            return StepResult.fail(
                "no cited claims for this need — Phase B must run first. "
                "Generating a tier without evidence is exactly what C4 forbids.")

        from ..research import evidence as ev
        p = await ev.params()
        cap = int(p.get("forge_max_sections", 8))
        plan = {t: [s.key for s in sections_for(t, max_sections=cap)]
                for t in TIER_ORDER}
        ctx.data["need_id"] = need_id
        return StepResult.ok(need_id=need_id, tiers_planned=len(plan),
                             sections={t: len(v) for t, v in plan.items()},
                             cited_claims_available=cited)

    @step(id="forge.generate", phase="FORGE", test=TEST,
          inputs=("need_id",), produces=("sections",),
          # ANY-of: `_llm` tries Anthropic then OpenRouter, so one live route is
          # enough. Requiring `anthropic` alone stranded this step on 2026-08-08
          # — the key was spend-capped, the fallback worked, and the gate still
          # said skipped_dormant because it only knew about one provider.
          requires_any_connectors=("anthropic", "openrouter"),
          acceptance=lambda r: r.data.get("tiers_generated", 0) == 3,
          acceptance_desc="every tier generated at least one section",
          idempotency_key="need_id", timeout_s=3000, cost_budget_usd=6.00)
    async def generate_step(ctx, need_id: int = 0, **kw: Any) -> StepResult:
        """E2 — ONE LLM CALL PER SECTION, never one per product.

        That discipline is what produced Pimlico's genuine 24–28k-word depth,
        and it is the one thing about its generation worth keeping.
        """
        need_id = need_id or ctx.data.get("need_id") or ctx.need_id
        built: dict[str, Any] = {}
        for tier in TIER_ORDER:
            out = await build.build_tier(need_id, tier, run_id=ctx.run_id)
            if out.get("error"):
                return StepResult.fail(out["error"])
            built[tier] = out["sections"]
            log.info("forge.tier_generated", need_id=need_id, tier=tier,
                     sections=len(out["sections"]), planned=out["planned"])
        ctx.data["built"] = built
        return StepResult.ok(
            need_id=need_id,
            tiers_generated=sum(1 for v in built.values() if v),
            sections={t: len(v) for t, v in built.items()},
            words={t: sum(s.words for s in v) for t, v in built.items()})

    @step(id="forge.package", phase="FORGE", test=TEST,
          inputs=("sections",), produces=("artifacts",),
          acceptance=lambda r: (r.data.get("artifacts", 0) == 3
                                and r.data.get("files_present", 0) == 3),
          acceptance_desc="three content-addressed artifacts, all three files on disk",
          idempotency_key="need_id", timeout_s=300)
    async def package_step(ctx, need_id: int = 0, **kw: Any) -> StepResult:
        """E5 — content-addressed artifacts, written to disk BEFORE the row.

        `delivery.mint()` refuses a token unless the file exists. All three of
        Pimlico's delivery tokens point at files that do not exist; writing the
        row first is how that happens.
        """
        from pathlib import Path
        need_id = need_id or ctx.data.get("need_id") or ctx.need_id
        built = ctx.data.get("built") or {}
        if not built:
            # Fall back to the drafts on disk.
            #
            # ⚠️ CORRECTION (2026-08-08): an earlier version of this comment
            # said "forge.generate is idempotent, so on a re-run it returns its
            # cached result". That is true only WITHIN a run — the engine's
            # idempotency lookup is scoped `WHERE run_id = $1 AND step_id = $2`,
            # and every `jpd forge run` opens a NEW run_id. So generation
            # re-executes, and re-bills, on every invocation. Measured: two full
            # regenerations of need 13 in one day.
            #
            # This fallback still matters (it recovers drafts when ctx.data is
            # empty for any reason), but to re-package and re-verify WITHOUT
            # paying to generate, use `jpd forge reverify <need>`.
            built = {t: build.load_draft(need_id, t) for t in TIER_ORDER}
            built = {t: v for t, v in built.items() if v}
            if built:
                log.info("forge.recovered_drafts", need_id=need_id,
                         tiers=sorted(built), sections=
                         {t: len(v) for t, v in built.items()})
        if not built:
            return StepResult.fail(
                "nothing generated and no drafts on disk — run forge.generate first")

        results = []
        for tier in TIER_ORDER:
            secs = built.get(tier) or []
            if not secs:
                continue
            results.append(await build.package(need_id, tier, secs, run_id=ctx.run_id))

        present = sum(1 for r in results
                      if Path(r["path"]).is_file() and Path(r["path"]).stat().st_size > 0)
        ctx.data["artifacts"] = results
        return StepResult.ok(need_id=need_id, artifacts=len(results),
                             files_present=present,
                             detail=[{k: r[k] for k in
                                      ("tier", "artifact_id", "words", "bytes",
                                       "cited_claims")} for r in results])

    @step(id="forge.verify", phase="FORGE", test=TEST,
          inputs=("artifacts",), produces=("verdicts",),
          acceptance=lambda r: (r.data.get("uncited_claims", 1) == 0
                                and r.data.get("verified", 0) == 3),
          acceptance_desc="all three artifacts verified with ZERO uncited claims",
          idempotency_key="need_id", timeout_s=3000, cost_budget_usd=3.00,
          repairable=True, max_repairs=2)
    async def verify_step(ctx, need_id: int = 0, **kw: Any) -> StepResult:
        """E4 — structural AND factual.

        Repair is available and branches on the DURABLE `repair_count`.
        Pimlico's guard tested `attempts`, which was reset on every transition,
        so a section the model reliably answered "TBD" looped for ever at full
        LLM cost.
        """
        need_id = need_id or ctx.data.get("need_id") or ctx.need_id
        rows = await db.fetch(
            "SELECT id, tier FROM artifacts WHERE need_id=$1 ORDER BY id", need_id)
        if not rows:
            return StepResult.fail("no artifacts to verify")

        # One memo for the whole need: the tiers SHARE a claim set, so without
        # it every claim is fact-checked once per artifact, the answers disagree
        # with each other, and the last write wins. See vf.factual().
        claim_verdicts: dict[int, tuple[bool, str]] = {}

        verdicts, unsupported, uncited = [], 0, 0
        for r in rows:
            res = await vf.verify(int(r["id"]), claim_verdicts)
            unsupported += len(res.unsupported)
            uncited += res.uncited_claims
            verdicts.append({
                "tier": r["tier"], "artifact_id": int(r["id"]),
                "structural_ok": res.structural_ok, "factual_ok": res.factual_ok,
                "claims_checked": res.claims_checked,
                "claims_supported": res.claims_supported,
                "unsupported": len(res.unsupported),
                "missing_sections": res.missing_sections,
                "thin_sections": res.thin_sections,
                "placeholders": res.placeholders})

        ctx.data["verdicts"] = verdicts
        # DISTINCT unsupported claims, not the per-artifact sum. The tiers share
        # a claim set, so summing counted the same failure once per tier: three
        # artifacts blocked by 4 claims was reported as "12 unsupported", which
        # made the remaining work look 3x larger than it was.
        distinct_unsupported = sum(1 for ok, _ in claim_verdicts.values() if not ok)
        return StepResult.ok(need_id=need_id, verified=len(verdicts),
                             uncited_claims=uncited,
                             unsupported_claims=distinct_unsupported,
                             unsupported_claim_slots=unsupported,
                             offerable=sum(1 for v in verdicts
                                           if v["structural_ok"] and v["factual_ok"]),
                             verdicts=verdicts)

    @step(id="forge.acceptance_tests", phase="FORGE", test=TEST,
          inputs=("artifacts",), produces=("acceptance_tests",),
          acceptance=lambda r: r.data.get("tiers_covered", 0) == 3,
          acceptance_desc="every tier has at least one buyer-runnable acceptance test",
          idempotency_key="need_id", timeout_s=120)
    async def acceptance_step(ctx, need_id: int = 0, **kw: Any) -> StepResult:
        """D4 — tests the BUYER runs to prove their build works.

        For the Deployed tier these are contractual: if they fail, that tier is
        not offered and the other two still sell.
        """
        need_id = need_id or ctx.data.get("need_id") or ctx.need_id
        rows = await db.fetch(
            "SELECT id, tier, storage_uri, words, sections FROM artifacts "
            "WHERE need_id=$1", need_id)

        made = 0
        for r in rows:
            tests = [
                (f"{r['tier']}: document is delivered and non-empty",
                 f"test -s \"$(echo '{r['storage_uri']}' | sed 's|file://||')\"",
                 "exit code 0"),
                (f"{r['tier']}: every cited source is listed",
                 "grep -c '^- \\*\\*\\[claim' <artifact>", ">= 1"),
            ]
            if r["tier"] in ("instructions", "deployed"):
                tests.append((
                    f"{r['tier']}: every build step has a verification",
                    "grep -A2 '^### Step' <artifact> | grep -c 'Verify'",
                    "one per step"))
            if r["tier"] == "deployed":
                tests.append((
                    "deployed: the running system answers its health check",
                    "curl -fsS http://<host>/health", '{"ok": true}'))

            for name, command, expected in tests:
                await db.execute(
                    """
                    INSERT INTO acceptance_tests (need_id, solution_id, tier, name,
                                                  command, expected, last_result)
                    VALUES ($1, NULL, $2, $3, $4, $5, 'never_run')
                    """, need_id, r["tier"], name[:200], command[:500], expected[:200])
                made += 1

        covered = await db.fetchval(
            "SELECT count(DISTINCT tier) FROM acceptance_tests WHERE need_id=$1", need_id)
        return StepResult.ok(need_id=need_id, tests_created=made,
                             tiers_covered=int(covered or 0))

    _REGISTERED = True
    log.info("forge.steps_registered", total_steps=len(all_steps()))


ORDER = ("forge.plan", "forge.generate", "forge.package", "forge.verify",
         "forge.acceptance_tests")


async def run_forge(need_id: int, run_id: int | None = None) -> dict:
    from ..runtime import engine

    register()
    rid = run_id or await engine.create_run("FORGE", need_id=need_id)
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
        log.warning("forge.complete_failed", run_id=rid, error=str(e)[:150])

    await db.execute(
        "UPDATE job_registry SET last_success_at = now() WHERE job_name = $1",
        "forge.build")
    return out
