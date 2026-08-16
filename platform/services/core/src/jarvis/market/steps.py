"""Phase F as `@step` units — MARKET.

    market.position      positioning in the buyer's own words, from a voice
    market.copy          headline/subhead/benefits/objections/faq, per tier
    market.copy_variants SINTRA instruction card → #sintra   (human)
    market.media         sizzle image / showcase video       (opt-in, costs money)
    market.pages         the self-hosted sales page, three offers one checkout
    market.launch        approval-gated outreach to the voices  (human decision)

🔴 THE ACCEPTANCE PREDICATE THAT MATTERS IS `market.copy`'s.

It requires citation coverage at or above the floor. Phase F is where an
uncited claim becomes a public promise, and on 2026-08-09 the first real
measurement of the phase C/D/E artifacts put coverage at 56.3% while the system
was reporting "0 uncited claims" — a NOT NULL constraint mistaken for a
verification result. Copy is gated on the measurement. Artifacts still are not,
because changing that would silently redefine `offerable`.
"""
from __future__ import annotations

import os
from typing import Any

import structlog

from .. import db
from ..runtime.registry import all_steps, step
from ..runtime.types import StepResult
from . import copy as copy_mod, outreach, pages

log = structlog.get_logger("market.steps")

TEST = "tests/stages/test_market_steps.py"
_REGISTERED = False


def register() -> None:
    global _REGISTERED
    if _REGISTERED and "market.position" in all_steps():
        return

    # ── F1 ────────────────────────────────────────────────────────────────
    @step(id="market.position", phase="MARKET", test=TEST,
          inputs=("need_id",), produces=("positioning",),
          acceptance=lambda r: bool(r.data.get("promise")),
          acceptance_desc="positioning exists, grounded in a cited claim",
          idempotency_key="need_id", timeout_s=600, cost_budget_usd=0.50)
    async def position_step(ctx, need_id: int = 0, **kw: Any) -> StepResult:
        need_id = need_id or ctx.data.get("need_id") or ctx.need_id
        if not need_id:
            return StepResult.fail("no need_id supplied")
        try:
            out = await copy_mod.build_positioning(int(need_id))
        except (ValueError, LookupError) as e:
            return StepResult.fail(str(e))
        ctx.data["positioning"] = out
        return StepResult.ok(**out)

    # ── F2 ────────────────────────────────────────────────────────────────
    @step(id="market.copy", phase="MARKET", test=TEST,
          inputs=("positioning",), produces=("copy",),
          acceptance=lambda r: (r.data.get("blocks_stored", 0) > 0
                                and r.data.get("below_floor", 1) == 0),
          acceptance_desc=("every copy block cites its factual claims — "
                           f"coverage >= {copy_mod.COVERAGE_FLOOR}%"),
          idempotency_key="need_id", timeout_s=3000, cost_budget_usd=4.00)
    async def copy_step(ctx, need_id: int = 0, **kw: Any) -> StepResult:
        """One block per tier per section. A block below the floor is REPORTED
        and stored, not silently dropped — the operator needs to see which
        sentence could not be cited, and the acceptance predicate fails."""
        need_id = int(need_id or ctx.data.get("need_id") or ctx.need_id or 0)
        if not need_id:
            return StepResult.fail("no need_id supplied")

        pos = await db.fetchrow(
            "SELECT pain_phrase, audience, promise, proof FROM positioning "
            " WHERE need_id=$1", need_id)
        if pos is None:
            return StepResult.fail("no positioning — run market.position first")

        claims = await copy_mod._evidence_pack(need_id)
        if not claims:
            return StepResult.fail(
                "no SUPPORTED claims — copy would be invention. The forge's "
                "factual pass must have produced supported claims first.")

        stored, below, worst = 0, [], 100.0
        for tier in pages.TIER_ORDER:
            for block in copy_mod.BLOCKS:
                b = await copy_mod.build_block(need_id, tier, block,
                                               claims, dict(pos))
                if not b["body"]:
                    below.append(f"{tier}/{block}: empty")
                    continue
                await copy_mod.store_block(need_id, b, run_id=ctx.run_id)
                stored += 1
                worst = min(worst, b["citation_pct"])
                if b["citation_pct"] < copy_mod.COVERAGE_FLOOR:
                    below.append(
                        f"{tier}/{block}: {b['citation_pct']:.0f}% "
                        f"({b['citation_checkable']} checkable)")

        ctx.data["copy"] = {"blocks": stored}
        return StepResult.ok(need_id=need_id, blocks_stored=stored,
                             below_floor=len(below), worst_coverage=worst,
                             floor=copy_mod.COVERAGE_FLOOR, detail=below[:12])

    # ── F3 ────────────────────────────────────────────────────────────────
    @step(id="market.copy_variants", phase="MARKET", test=TEST,
          inputs=("copy",), produces=("variants",),
          acceptance=lambda r: bool(r.data.get("ref") or r.data.get("skipped")),
          acceptance_desc="a Sintra instruction card is posted, or explicitly skipped",
          idempotency_key="need_id", timeout_s=300)
    async def variants_step(ctx, need_id: int = 0, **kw: Any) -> StepResult:
        """Sintra is a HUMAN connector — Cloudflare-blocked from this host. The
        card carries a prompt built from real copy, and nothing Sintra-shaped
        can auto-publish."""
        from ..console import human
        need_id = int(need_id or ctx.data.get("need_id") or ctx.need_id or 0)
        head = await db.fetchrow(
            "SELECT body FROM copy_blocks WHERE need_id=$1 AND tier='roadmap' "
            "  AND block='headline'", need_id)
        if head is None:
            return StepResult.fail("no headline to vary — run market.copy first")

        r = await human.sintra(
            key=f"market-variants-{need_id}",
            why=("Three headline variants for A/B testing. Sintra is "
                 "Cloudflare-blocked from this host, so this is a paste-and-reply "
                 "step by design."),
            bot="Copywriter",
            prompt=("Write 3 alternative headlines for this product page. Keep "
                    "every factual claim identical — do not add numbers or "
                    "superlatives that are not already present.\n\n"
                    f"CURRENT HEADLINE:\n{head['body'][:900]}"),
            run_id=ctx.run_id, step_id="market.copy_variants")
        return StepResult.ok(need_id=need_id, ref=r.ref, state=r.state)

    # ── F4 ────────────────────────────────────────────────────────────────
    @step(id="market.media", phase="MARKET", test=TEST,
          inputs=("copy",), produces=("media",),
          acceptance=lambda r: bool(r.data.get("skipped") or r.data.get("assets")),
          acceptance_desc="media generated, or explicitly skipped with a reason",
          idempotency_key="need_id", timeout_s=1800)
    async def media_step(ctx, need_id: int = 0, **kw: Any) -> StepResult:
        """OPT-IN, because it spends real money on every run.

        The sizzle image is ~$0.039. The showcase video is ~544 credits per 8s
        clip — MEASURED; SuperCool's own docs claim 68 and are wrong by 8×, which
        is exactly the kind of number that turns a demo into a bill.

        Default is skip. Enabling is a deliberate config change, and video has
        its own second flag on top.
        """
        need_id = int(need_id or ctx.data.get("need_id") or ctx.need_id or 0)
        if os.environ.get("JPD_MEDIA_ENABLED", "false").lower() != "true":
            return StepResult.ok(
                need_id=need_id, skipped=True,
                reason="JPD_MEDIA_ENABLED is not true — media generation spends "
                       "real money per run and is opt-in by design")
        video = os.environ.get("JPD_SHOWCASE_VIDEO_ENABLED", "false").lower() == "true"
        return StepResult.ok(
            need_id=need_id, skipped=True,
            reason=("media generation is enabled but no image/video provider is "
                    "configured on this host; wire one before expecting assets"),
            video_enabled=video)

    # ── F5 ────────────────────────────────────────────────────────────────
    @step(id="market.pages", phase="MARKET", test=TEST,
          inputs=("copy",), produces=("page",),
          acceptance=lambda r: bool(r.data.get("path")) and r.data.get("tiers", 0) > 0,
          acceptance_desc="a content-addressed sales page exists on disk",
          idempotency_key="need_id", timeout_s=300)
    async def pages_step(ctx, need_id: int = 0, **kw: Any) -> StepResult:
        need_id = int(need_id or ctx.data.get("need_id") or ctx.need_id or 0)
        try:
            out = await pages.build_page(need_id, run_id=ctx.run_id)
        except ValueError as e:
            return StepResult.fail(str(e))
        ctx.data["page"] = out
        # Building is not publishing. `publishable` is reported, never acted on
        # here — a page goes live behind a human decision.
        return StepResult.ok(**out)

    # ── F5b ───────────────────────────────────────────────────────────────
    @step(id="market.launch", phase="MARKET", test=TEST,
          inputs=("page",), produces=("launch",),
          acceptance=lambda r: bool(r.data.get("ref") or r.data.get("refused")),
          acceptance_desc=("a launch is planned and either approval-gated or "
                           "refused with named reasons — never a partial send"),
          idempotency_key="need_id", timeout_s=600)
    async def launch_step(ctx, need_id: int = 0, **kw: Any) -> StepResult:
        """Plans the list, then STOPS for a human decision.

        This step never sends. It produces an auditable plan and a decision
        card; sending is a separate, explicit act.
        """
        from ..console import human
        need_id = int(need_id or ctx.data.get("need_id") or ctx.need_id or 0)
        base = os.environ.get("JPD_UNSUBSCRIBE_BASE", "")
        if not base:
            return StepResult.fail(
                "JPD_UNSUBSCRIBE_BASE is not set — refusing to plan outreach "
                "without an unsubscribe path for every recipient")

        plan = await outreach.plan_launch(need_id, base, run_id=ctx.run_id)
        try:
            outreach.assert_sendable(plan)
        except PermissionError as e:
            # A refusal is a SUCCESSFUL outcome for this step: the compliance
            # stop worked and is recorded. It is not a step failure to report
            # that a community-scraped audience cannot lawfully be mailed.
            log.warning("market.launch_refused", need_id=need_id,
                        blocked=len(plan["blocked"]),
                        eligible=len(plan["eligible"]))
            return StepResult.ok(need_id=need_id, refused=True, reason=str(e)[:600],
                                 eligible=len(plan["eligible"]),
                                 blocked=len(plan["blocked"]),
                                 competitors=len(plan["excluded"]))

        r = await human.decide(
            key=f"market-launch-{need_id}",
            question=(f"Send launch outreach to {len(plan['eligible'])} voices "
                      f"who described this problem?"),
            why=("Every recipient has a recorded lawful basis, an unsubscribe "
                 "path and a citation to their own quote. Outreach is opt-in "
                 "per launch and never a standing automation."),
            options=["send", "cancel"],
            run_id=ctx.run_id, step_id="market.launch")
        return StepResult.ok(need_id=need_id, ref=r.ref, state=r.state,
                             eligible=len(plan["eligible"]),
                             competitors=len(plan["excluded"]))

    _REGISTERED = True
    log.info("market.steps_registered", total_steps=len(all_steps()))


# Phase F in dependency order. `market.media` sits after copy because a sizzle
# image is generated FROM the promise, and before pages because the page embeds
# it once it exists.
ORDER = ("market.position", "market.copy", "market.copy_variants",
         "market.media", "market.pages", "market.launch")


async def run_market(need_id: int, run_id: int | None = None,
                     stop_after: str | None = None) -> dict:
    """Phase F end to end, or up to `stop_after`.

    `stop_after` exists because the two ends of this phase have very different
    consequences: `market.position` and `market.copy` spend LLM budget, and
    `market.launch` reaches real people. Running "everything" is rarely what an
    operator wants on a first pass, and a flag is cheaper than a half-finished
    run they have to unpick.
    """
    from ..runtime import engine
    from ..runtime.types import StepStatus

    register()
    rid = run_id or await engine.create_run("MARKET", need_id=need_id)
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
        if stop_after and sid == stop_after:
            out["stopped_after"] = sid
            break

    try:
        await engine.complete_run(
            ctx, "completed" if "stopped_at" not in out else "failed")
    except Exception as e:                                       # noqa: BLE001
        log.warning("market.complete_failed", run_id=rid, error=str(e)[:150])
    return out
