"""F5b — launch to the people who described the problem.

The highest-conversion audience for a solution is the people who said they had
the problem. That is also why this is the most dangerous step in the pipeline:
those people are in the database because they posted on Reddit or filed a GitHub
issue, NOT because they asked to hear from us.

────────────────────────────────────────────────────────────────────────────
HARD RULES — 02-ARCHITECTURE.md, non-negotiable
────────────────────────────────────────────────────────────────────────────
· `do_not_contact` defaults TRUE for anything scraped from a community
  platform. Promotion to contactable requires an explicit lawful basis recorded
  PER VOICE. Reddit/HN/App Store authors are evidence, never a mailing list.
· Outreach is approval-gated, never automatic. Opt-in per launch.
· `sells_alternative` voices are competitors and are excluded automatically.

────────────────────────────────────────────────────────────────────────────
IT FAILS, IT DOES NOT FILTER
────────────────────────────────────────────────────────────────────────────
F5b's acceptance is that EVERY recipient has a lawful basis, an unsubscribe
path, and a citation to their own quote — and that the step REFUSES if any
candidate fails, naming who and why.

Silently dropping the failures would be the tempting design and it is wrong:
it turns a compliance stop into a smaller send that looks successful, and
nobody ever learns the list was unusable. A refusal is visible. A quiet
90%-of-a-send is not.

Measured 2026-08-09: 218 voice mentions on this platform, **0 contactable**.
This step correctly sends nothing today, and will keep sending nothing until a
human records a lawful basis per voice.
"""
from __future__ import annotations

from typing import Any, Optional

import structlog

from .. import db

log = structlog.get_logger("market.outreach")

# Stance → the tier that matches what they actually said (03-PIPELINE F5b).
STANCE_TIER = {
    "requests_solution": "deployed",      # they asked for the thing
    "reports_pain": "instructions",       # problem + some capability
    "offers_workaround": "roadmap",       # already building; sell the plan
    "endorses": "roadmap",
}
EXCLUDED_STANCES = ("sells_alternative",)  # competitors


async def plan_launch(need_id: int, unsubscribe_base: str,
                      run_id: Optional[int] = None) -> dict[str, Any]:
    """Build the intended recipient list and persist it BEFORE anything sends.

    Persisting the plan first is what makes a refusal auditable after the fact:
    the list that was intended is recoverable even when nothing was sent.
    """
    if not unsubscribe_base:
        raise ValueError(
            "no unsubscribe base url — refusing to plan outreach without an "
            "unsubscribe path for every recipient")

    rows = await db.fetch(
        """
        SELECT vm.voice_id, vm.stance, vm.quote, vm.evidence_id,
               v.display_name, v.contactable, v.do_not_contact,
               v.contact_ref, v.lawful_basis, v.kind, v.platform
          FROM voice_mentions vm
          JOIN voices v ON v.id = vm.voice_id
         WHERE vm.need_id = $1
         ORDER BY vm.voice_id
        """, need_id)

    candidates, excluded, blocked = [], [], []
    seen: set[int] = set()
    for r in rows:
        if r["voice_id"] in seen:
            continue
        seen.add(r["voice_id"])

        if r["stance"] in EXCLUDED_STANCES:
            excluded.append({"voice_id": r["voice_id"], "why": "competitor "
                             f"({r['stance']})"})
            continue

        tier = STANCE_TIER.get(r["stance"])
        if not tier:
            excluded.append({"voice_id": r["voice_id"],
                             "why": f"no tier mapping for stance {r['stance']!r}"})
            continue

        # Each of these is a HARD stop, and each is reported by name.
        reasons = []
        if r["do_not_contact"]:
            reasons.append("do_not_contact is set")
        if not r["contactable"]:
            reasons.append("not marked contactable")
        if not (r["lawful_basis"] or "").strip():
            reasons.append("no lawful basis recorded")
        if not (r["contact_ref"] or "").strip():
            reasons.append("no contact_ref")
        if not (r["quote"] or "").strip():
            reasons.append("no quote to cite back")
        if reasons:
            blocked.append({"voice_id": r["voice_id"],
                            "name": r["display_name"], "why": "; ".join(reasons)})
            continue

        candidates.append({
            "voice_id": r["voice_id"], "tier": tier, "stance": r["stance"],
            "quote": r["quote"], "evidence_id": r["evidence_id"],
            "lawful_basis": r["lawful_basis"], "contact_ref": r["contact_ref"],
            "unsubscribe_url": f"{unsubscribe_base.rstrip('/')}/u/{r['voice_id']}",
        })

    for c in candidates:
        await db.execute(
            """
            INSERT INTO launch_recipients (need_id, voice_id, tier, stance, quote,
                evidence_id, lawful_basis, contact_ref, unsubscribe_url, run_id)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (need_id, voice_id) DO UPDATE SET
              tier=EXCLUDED.tier, stance=EXCLUDED.stance, quote=EXCLUDED.quote,
              lawful_basis=EXCLUDED.lawful_basis,
              contact_ref=EXCLUDED.contact_ref,
              unsubscribe_url=EXCLUDED.unsubscribe_url, status='planned'
            """,
            need_id, c["voice_id"], c["tier"], c["stance"], c["quote"][:2000],
            c["evidence_id"], c["lawful_basis"], c["contact_ref"],
            c["unsubscribe_url"], run_id)

    log.info("market.launch_planned", need_id=need_id, eligible=len(candidates),
             blocked=len(blocked), competitors=len(excluded))
    return {"need_id": need_id, "eligible": candidates, "blocked": blocked,
            "excluded": excluded,
            "total_voices": len(seen)}


def assert_sendable(plan: dict[str, Any]) -> None:
    """Refuse the launch if ANY candidate failed a compliance check.

    Not "send to the ones that passed". The whole point of F5b's acceptance is
    that a partial send hides an unusable list behind an apparently successful
    step.
    """
    if plan["blocked"]:
        names = "; ".join(
            f"voice {b['voice_id']} ({b.get('name') or 'unnamed'}): {b['why']}"
            for b in plan["blocked"][:10])
        raise PermissionError(
            f"{len(plan['blocked'])} of {plan['total_voices']} voices fail the "
            f"outreach checks — REFUSING the launch rather than sending to a "
            f"subset. Fix or exclude each, then re-run. {names}")
    if not plan["eligible"]:
        raise PermissionError(
            "no eligible recipients — every voice is do_not_contact, lacks a "
            "lawful basis, or is a competitor. This is the expected state for a "
            "community-scraped audience and is not a bug.")


def compose(recipient: dict[str, Any], pos: dict[str, Any],
            checkout_url: str) -> str:
    """Their own words back, with the citation, and an unsubscribe path."""
    return "\n".join([
        f"You wrote: \"{recipient['quote'][:300]}\"",
        "",
        f"That is the problem this solves — {pos.get('promise', '')}",
        "",
        f"For someone in your position the {recipient['tier']} tier is the fit.",
        checkout_url,
        "",
        f"Source of the quote above: evidence #{recipient.get('evidence_id')}",
        f"Never hear from us again: {recipient['unsubscribe_url']}",
    ])
