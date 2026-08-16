"""F1–F2 — positioning and per-tier sales copy, from evidence.

────────────────────────────────────────────────────────────────────────────
THE RULE THIS MODULE EXISTS TO ENFORCE
────────────────────────────────────────────────────────────────────────────
Phase F is where an uncited claim stops being an internal quality problem and
becomes a PUBLIC PROMISE. `03-PIPELINE.md` F2 says "every factual claim cited",
and until 2026-08-09 the system believed it was already achieving that: it
reported "0 uncited claims" on every artifact. That number was a NOT NULL
constraint on `claims.evidence_id`, not a measurement. The first real
measurement of the phase C/D/E artifacts put citation coverage at 56.3%.

So copy is GATED on coverage and artifacts are not. That asymmetry is
deliberate: gating artifacts would silently change what `offerable` means, and
the blast radius of an uncited sentence in an internal deliverable is a reader
who is misled. The blast radius of an uncited sentence in a headline is a
promise made to strangers who paid.

Positioning uses the buyer's OWN WORDS — the `voices` quotes captured in phase
A — not invented adjectives. If no voice said it, we do not claim it.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import structlog

from .. import db
from ..forge.build import _llm, find_placeholders
from ..forge.verify import citation_coverage

log = structlog.get_logger("market.copy")


async def _offer_prices_minor(need_id: int) -> frozenset[int]:
    """Every offer price for the need, for the offer-description carve-out.

    The whole ladder, not just one tier — a block legitimately names its own
    price and an upgrade tier's, and both must match the checkout's reality.
    """
    rows = await db.fetch(
        "SELECT o.price_minor FROM offers o "
        "  JOIN solutions s ON s.id = o.solution_id WHERE s.need_id = $1",
        need_id)
    return frozenset(int(r["price_minor"]) for r in rows)

# The floor a block must clear to be stored as approved-shape copy.
# Chosen against measured data: the phase C/D/E artifacts sit at 46–76%, so a
# floor of 90 is a real bar rather than a rubber stamp, and copy is short enough
# that citing nearly everything checkable is achievable — unlike a 5,000-word
# build manual.
COVERAGE_FLOOR = 90.0

BLOCKS = ("headline", "subhead", "benefits", "objections", "faq")

# 🔴 WHAT IS ACTUALLY BEING SOLD: A DOCUMENT.
#
# The first framing described the deployed tier as "a built, configured, tested,
# handed-over system", which is the tier LADDER's generic definition. The model
# read that as a service and wrote copy promising things a seller would have to
# perform: "We restore your login", "We can get you access restored within 48
# hours", "Most cases resolve within [X business days — needs data]".
#
# Those cannot be cited, and no evidence could ever cite them — the evidence is
# App Store reviews of someone else's broken app. Measured 2026-08-09: `benefits`
# and `faq` failed the coverage floor on ALL THREE tiers while `headline`,
# `subhead` and `objections` mostly passed, because the first two described a
# SERVICE and the rest described the PROBLEM.
#
# The deliverable is a document. A benefit is therefore something the document
# CONTAINS or something the READER can do after reading it — never something
# "we" do for them.
DELIVERABLE = (
    "You are selling a WRITTEN DOCUMENT that the buyer downloads and follows "
    "themselves. There is no service, no support desk, no account team, and "
    "nobody acting on the buyer's behalf.")

TIER_BUYER = {
    "roadmap": ("Someone who will act themselves and needs to know what to do, "
                "in what order, at what cost. They receive a plan document."),
    "instructions": ("Someone who will execute and needs the full manual — exact "
                     "steps, configuration, templates. They receive a build "
                     "manual document."),
    "deployed": ("Someone who wants the most complete version: the manual plus "
                 "as-built detail, runbook and acceptance checks. They still "
                 "receive a DOCUMENT, not a system somebody else operates."),
}

BLOCK_BRIEF = {
    "headline": "One line. The outcome in the buyer's language. No colon-subtitle "
                "construction, no superlatives you cannot cite.",
    "subhead": "One or two sentences. Who it is for and what changes for them.",
    "benefits": "Three to five bullets. Each names something THE DOCUMENT "
                "CONTAINS, or something the reader can do once they have it — "
                "'the exact escalation sequence, with the wording for each "
                "step', not 'we escalate for you'. Every claim about the world "
                "outside the document carries a citation.",
    "objections": "The three objections this buyer actually raises, each answered "
                  "honestly. An objection you cannot answer is stated as a limit.",
    "faq": "Four to six questions a real buyer asks before paying. Answer them "
           "plainly, including price and what is NOT included. Questions about "
           "outcomes are answered in terms of what the document gives them to "
           "do — never a promised result, a turnaround time, or a success rate, "
           "because none of those can be cited and none of them are being sold.",
}

# First-person service language. These are not style violations; each one is a
# promise the seller cannot keep, because the product is a file.
#   · up to two words may sit between the subject and the verb, so "we CAN get
#     you access" and "we WILL ALSO restore" are caught, not just bare "we get".
#   · the verb carries an open suffix, so "monitors", "handled", "escalating"
#     all match the one stem.
# Both gaps were found by the tests below rather than by reading the regex.
_SERVICE_PROMISE = re.compile(
    r"(?i)(?:\b(?:we|we'll|we've|our\s+team|our\s+specialists?)\b"
    r"(?:\s+\w+){0,2}\s+"
    r"(?:restore|recover|resolve|handle|manage|escalate|contact|call|negotiate|"
    r"fix|configure|deploy|implement|monitor|support|provide|deliver|guarantee|"
    r"refund|chase|liaise|set\s?up|take\s+care|get\s+you)\w*)"
    r"|\b(?:done[-\s]for[-\s]you|on your behalf|account manager|"
    r"dedicated support)\b")


# 🔴 A DENIAL IS NOT A PROMISE.
#
# The fixed prompt made the copy say exactly what it should — "there is no
# account team", "no support desk, nobody acting on your behalf", "the document
# does not contact the vendor for you". The first version of this checker
# flagged every one of those, because it matched the phrase and ignored the
# negation in front of it.
#
# That is the fourth false positive of this shape today (a placeholder rule
# rejecting buyer fields, a tautological uncited count, a 598-word section
# called thin). The pattern is worth naming: a checker that punishes the correct
# behaviour trains people to disable it, which is strictly worse than not having
# it. Measured 2026-08-09 — 3 of 7 regenerated blocks were flagged for
# correctly DENYING that a service exists.
_NEGATED = re.compile(
    r"(?i)\b(no|not|never|nobody|nothing|without|isn't|is not|aren't|are not|"
    r"won't|will not|doesn't|does not|don't|do not|cannot|can't)\b")


def service_promises(text: str) -> list[str]:
    """Sentences that promise the SELLER will act. Copy sells a document.

    Mechanical because the coverage metric cannot catch this: "We restore your
    login within 48 hours" is uncitable, so it already fails coverage — but
    "We'll handle it" contains nothing checkable at all and would pass a
    coverage gate while being the most dangerous sentence on the page.

    A NEGATED mention is not a promise. "There is no account team" is the copy
    doing its job; flagging it would punish the fix.
    """
    out = []
    for sent in re.split(r"(?<=[.!?])\s+", text or ""):
        m = _SERVICE_PROMISE.search(sent)
        if not m:
            continue
        # The negation may sit INSIDE the match ("we will not escalate"), so
        # scan up to the match END, not merely up to its start.
        head = sent[:m.end()]
        negated = bool(_NEGATED.search(head))
        # ...but a coordinator immediately before the match starts a NEW,
        # positive clause, and an earlier negation does not reach into it:
        #   "There is no setup fee, and we restore your login"  <- a promise
        # versus
        #   "There is no account team, or anyone acting on your behalf"  <- not
        if negated and re.search(r"\b(?:and|plus)\b[^,;]{0,40}$", sent[:m.start()],
                                 re.I):
            negated = False
        if negated:
            continue
        out.append(sent.strip()[:160])
    return out[:5]


async def _evidence_pack(need_id: int, limit: int = 14) -> list[dict]:
    """Supported claims only. Copy is built from what survived verification.

    Unsupported claims are excluded on purpose: a claim the fact-checker
    REJECTED is the last thing that should end up in a headline.
    """
    rows = await db.fetch(
        """
        SELECT DISTINCT c.id, c.text, e.url
          FROM claims c
          JOIN evidence e ON e.id = c.evidence_id
         WHERE c.need_id = $1 AND c.supported IS TRUE
           AND e.substantive AND e.live_at_capture
         ORDER BY c.id
         LIMIT $2
        """, need_id, limit)
    return [dict(r) for r in rows]


async def _voice_quotes(need_id: int, limit: int = 8) -> list[dict]:
    """The buyer's own words. `sells_alternative` is excluded — a competitor's
    framing of the problem is not positioning, it is their marketing."""
    rows = await db.fetch(
        """
        SELECT vm.quote, vm.stance, vm.evidence_id, v.id AS voice_id,
               v.display_name, v.platform
          FROM voice_mentions vm
          JOIN voices v ON v.id = vm.voice_id
         WHERE vm.need_id = $1
           AND vm.stance <> 'sells_alternative'
           AND coalesce(vm.quote, '') <> ''
         ORDER BY vm.observed_at DESC NULLS LAST
         LIMIT $2
        """, need_id, limit)
    return [dict(r) for r in rows]


def _cited_ids(text: str) -> list[int]:
    # Tolerates the misspelled marker — see forge/verify.py _CITE.
    return sorted({int(m) for m in
                   re.findall(r"\[cla[a-z]{2} (\d+)\]", text or "", re.I)})


# `needs.audience` is not a description of a buyer. Observed on need 13 it held
# the literal string "5 distinct voices" — a COUNT written into a field meant to
# hold a segment, by phase A qualification. F1 read it, found it useless, and
# invented "Finance teams at companies with 50-500 employees" to fill the gap —
# a segment the deliverable barely addresses, while the artifacts are written
# for the 1-10 person owner-operator. Fifteen copy blocks would have been
# written for the wrong buyer at a price ladder anchored for a different one.
_PLACEHOLDER_AUDIENCE = re.compile(
    r"^\s*(\d+\s*(distinct\s*)?(voices?|signals?|mentions?|sources?)|n/?a|tbd|"
    r"unknown|none)\s*$", re.I)


def usable_audience(value: Optional[str]) -> bool:
    """False for counts, placeholders and anything too short to be a segment."""
    v = (value or "").strip()
    return bool(v) and len(v) >= 12 and not _PLACEHOLDER_AUDIENCE.match(v)


async def product_audience(need_id: int) -> str:
    """The audience the PRODUCT actually addresses, from its own text.

    The forge writes a `Who This Is For` section per tier, generated from the
    research and verified against it. That section is the product's own
    identification of its buyer, so it is a far better source than a needs
    column — and it cannot drift from the deliverable, because it IS the
    deliverable.
    """
    rows = await db.fetch(
        "SELECT tier, storage_uri FROM artifacts WHERE need_id=$1 ORDER BY id",
        need_id)
    from pathlib import Path
    for r in rows:
        p = Path((r["storage_uri"] or "").replace("file://", ""))
        if not p.is_file():
            continue
        text = p.read_text(errors="replace")
        m = re.search(r"^#{1,3}\s*Who This Is For\s*$(.*?)(?=^#{1,3}\s|\Z)",
                      text, re.S | re.M | re.I)
        if m and m.group(1).strip():
            return m.group(1).strip()[:2500]
    return ""


async def build_positioning(need_id: int) -> dict[str, Any]:
    """F1 — positioning grounded in a named voice and a cited claim."""
    need = await db.fetchrow(
        "SELECT id, title, pain_statement, audience FROM needs WHERE id=$1", need_id)
    if need is None:
        raise LookupError(f"no need {need_id}")

    claims = await _evidence_pack(need_id)
    if not claims:
        raise ValueError(
            "no SUPPORTED claims for this need — positioning would be invention. "
            "Run phase B and the forge verification first.")
    voices = await _voice_quotes(need_id)

    quote_block = "\n".join(
        f"- \"{v['quote'][:220]}\" — {v['display_name'] or 'anonymous'} "
        f"on {v['platform'] or 'unknown'} ({v['stance']})" for v in voices) \
        or "(no voice quotes captured)"
    claim_block = "\n".join(f"[claim {c['id']}] {c['text'][:200]}" for c in claims)

    # Audience comes from the PRODUCT and the VOICES, never from needs.audience.
    prod_aud = await product_audience(need_id)
    fallback = need["audience"] if usable_audience(need["audience"]) else ""
    if not prod_aud and not fallback:
        raise ValueError(
            "cannot derive an audience: the artifacts have no 'Who This Is For' "
            f"section and needs.audience is unusable ({need['audience']!r}). "
            "Run the forge first — positioning must describe the buyer the "
            "PRODUCT addresses, not one invented to fill a gap.")

    aud_block = (f"THE PRODUCT'S OWN 'WHO THIS IS FOR' SECTION:\n{prod_aud}"
                 if prod_aud else f"RECORDED AUDIENCE: {fallback}")

    reply = await _llm(
        "You are writing POSITIONING for a product, from evidence only.\n\n"
        f"PRODUCT SOLVES: {need['title']}\n"
        f"PAIN STATEMENT: {need['pain_statement'] or ''}\n\n"
        f"{aud_block}\n\n"
        f"WHAT REAL PEOPLE SAID:\n{quote_block}\n\n"
        f"VERIFIED CLAIMS:\n{claim_block}\n\n"
        "Write positioning that uses THEIR language, not marketing adjectives.\n"
        "- pain_phrase: the problem in the buyer's own words, short\n"
        "- audience: 🔴 DERIVE THIS from the product's own 'Who This Is For' "
        "section and from who the quoted people actually are. If the product "
        "names several segments, choose the PRIMARY one — the one it is written "
        "for first. Do NOT invent a company size, a job title or a market "
        "segment that appears in neither source. Getting this wrong writes every "
        "downstream headline for the wrong buyer.\n"
        "- promise: the measurable change, no superlatives\n"
        "- proof: one sentence of evidence, citing [claim N]\n\n"
        'Reply with ONLY JSON: {"pain_phrase":"","audience":"","promise":"","proof":""}',
        max_tokens=700, model_param="verify_model")

    data = _parse_json(reply)
    if data is None:
        raise ValueError("positioning did not return usable JSON")

    v0 = voices[0] if voices else {}
    await db.execute(
        """
        INSERT INTO positioning (need_id, pain_phrase, audience, promise, proof,
                                 voice_id, evidence_id)
        VALUES ($1,$2,$3,$4,$5,$6,$7)
        ON CONFLICT (need_id) DO UPDATE SET
          pain_phrase=EXCLUDED.pain_phrase, audience=EXCLUDED.audience,
          promise=EXCLUDED.promise, proof=EXCLUDED.proof,
          voice_id=EXCLUDED.voice_id, evidence_id=EXCLUDED.evidence_id
        """,
        need_id, str(data.get("pain_phrase", ""))[:600],
        str(data.get("audience", ""))[:600], str(data.get("promise", ""))[:600],
        str(data.get("proof", ""))[:900], v0.get("voice_id"), v0.get("evidence_id"))

    log.info("market.positioned", need_id=need_id, voices=len(voices),
             claims=len(claims))
    return {"need_id": need_id, "voices_used": len(voices),
            "claims_available": len(claims), **data}


def _parse_json(reply: Optional[str]) -> Optional[dict]:
    if not reply:
        return None
    m = re.search(r"\{.*\}", reply, re.S)
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
        return out if isinstance(out, dict) else None
    except Exception:                                            # noqa: BLE001
        return None


async def build_block(need_id: int, tier: str, block: str,
                      claims: list[dict], pos: dict) -> dict[str, Any]:
    """F2 — one copy block for one tier, cited.

    Returns the block WITH its measured coverage. Storage is the caller's
    decision, because a block below the floor is a result worth reporting, not
    a row worth keeping.
    """
    claim_block = "\n".join(f"[claim {c['id']}] {c['text'][:200]}" for c in claims)
    reply = await _llm(
        f"Write the {block.upper()} block of a sales page.\n\n"
        f"{DELIVERABLE}\n\n"
        f"TIER: {tier} — {TIER_BUYER.get(tier, '')}\n"
        f"BRIEF: {BLOCK_BRIEF[block]}\n\n"
        f"POSITIONING\n"
        f"  pain: {pos.get('pain_phrase','')}\n"
        f"  audience: {pos.get('audience','')}\n"
        f"  promise: {pos.get('promise','')}\n\n"
        f"VERIFIED CLAIMS you may cite:\n{claim_block}\n\n"
        "RULES — these are not style preferences:\n"
        "- EVERY factual assertion about the world carries a [claim N] marker.\n"
        "- If you cannot cite it, do not assert it. Say what would need checking.\n"
        "- No superlatives, no invented statistics, no 'industry-leading'.\n"
        "- 🔴 NEVER promise that WE do something for the buyer. No 'we restore', "
        "no 'we handle it', no turnaround times, no success rates. The buyer "
        "downloads a document and acts on it themselves. Write what the "
        "document gives them.\n"
        "- Write for someone deciding whether to spend their own money.\n\n"
        "Reply with the block text only. Markdown allowed. No preamble.",
        max_tokens=900, model_param="verify_model")

    text = (reply or "").strip()
    cov = citation_coverage(
        text, offer_prices_minor=await _offer_prices_minor(need_id))
    promises = service_promises(text)
    # 🔴 Copy is checked for unfinished-work markers too. Artifacts have been
    # since the beginning; copy never was, and `[Price would go here]` was
    # sitting in a live FAQ block that passed every other gate.
    placeholders = find_placeholders(text)
    return {"tier": tier, "block": block, "body": text, "placeholders": placeholders,
            "citation_pct": cov["coverage_pct"],
            "citation_checkable": cov["checkable"],
            "cited_claim_ids": _cited_ids(text),
            "examples": cov["examples"],
            "service_promises": promises}


async def store_block(need_id: int, b: dict, run_id: Optional[int] = None) -> None:
    await db.execute(
        """
        INSERT INTO copy_blocks (need_id, tier, block, body, citation_pct,
                                 citation_checkable, cited_claim_ids, run_id)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        ON CONFLICT (need_id, tier, block) DO UPDATE SET
          body=EXCLUDED.body, citation_pct=EXCLUDED.citation_pct,
          citation_checkable=EXCLUDED.citation_checkable,
          cited_claim_ids=EXCLUDED.cited_claim_ids, run_id=EXCLUDED.run_id,
          approved_at=NULL
        """,
        need_id, b["tier"], b["block"], b["body"], b["citation_pct"],
        b["citation_checkable"], b["cited_claim_ids"], run_id)


async def recopy(need_id: int, tier: Optional[str] = None,
                 block: Optional[str] = None, below_floor_only: bool = False,
                 run_id: Optional[int] = None) -> list[dict]:
    """Regenerate SOME copy blocks — one, one tier's worth, or every failing one.

    🔴 WHY THIS EXISTS. `market.copy` writes 15 blocks at ~$4. Measured on
    need 13, SEVEN of those 15 already cleared the 90% floor and eight did not —
    so re-running the whole step to fix eight blocks pays twice for seven that
    were already right, and replaces them with fresh text that might not be.

    `below_floor_only=True` regenerates exactly the blocks that failed and
    leaves the passing ones untouched, which is also what makes the coverage
    number comparable between runs: the blocks that did not change cannot
    explain a change in the total.

    The regenerated block is stored either way — a block that is still below the
    floor is a RESULT the operator needs to read, not something to discard. It
    is stored with `approved_at` cleared, so nothing silently inherits approval.
    """
    pos = await db.fetchrow(
        "SELECT pain_phrase, audience, promise, proof FROM positioning "
        " WHERE need_id=$1", need_id)
    if pos is None:
        raise ValueError("no positioning — run `jpd market position` first")

    claims = await _evidence_pack(need_id)
    if not claims:
        raise ValueError("no SUPPORTED claims — copy would be invention")

    rows = await db.fetch(
        "SELECT tier, block, citation_pct FROM copy_blocks WHERE need_id=$1",
        need_id)
    existing = {(r["tier"], r["block"]): float(r["citation_pct"]) for r in rows}

    from .pages import TIER_ORDER
    targets: list[tuple[str, str]] = []
    for t in TIER_ORDER:
        if tier and t != tier:
            continue
        for b in BLOCKS:
            if block and b != block:
                continue
            if below_floor_only and existing.get((t, b), 0.0) >= COVERAGE_FLOOR:
                continue
            targets.append((t, b))

    if not targets:
        return []

    out = []
    for t, b in targets:
        gen = await build_block(need_id, t, b, claims, dict(pos))
        if not gen["body"]:
            out.append({**gen, "stored": False, "before": existing.get((t, b))})
            continue
        await store_block(need_id, gen, run_id=run_id)
        out.append({**gen, "stored": True, "before": existing.get((t, b))})

    log.info("market.recopied", need_id=need_id, blocks=len(out),
             below_floor_only=below_floor_only)
    return out


async def remeasure(need_id: int) -> dict[str, Any]:
    """Re-run the coverage measurement over STORED blocks. Free — no LLM call.

    Exists because the measurement itself can change (carve-outs are operator
    decisions), and regenerating correct text to satisfy a changed metric pays
    an LLM for work that was already right — the lesson `jpd forge repair`
    earned on the four-word "thin" section. `approved_at` is left alone:
    remeasuring is bookkeeping, not new copy.
    """
    prices = await _offer_prices_minor(need_id)
    rows = await db.fetch(
        "SELECT id, tier, block, body, citation_pct FROM copy_blocks "
        " WHERE need_id=$1 ORDER BY tier, block", need_id)
    changed, below = 0, []
    for r in rows:
        cov = citation_coverage(r["body"], offer_prices_minor=prices)
        if float(cov["coverage_pct"]) != float(r["citation_pct"]):
            changed += 1
        await db.execute(
            "UPDATE copy_blocks SET citation_pct=$2, citation_checkable=$3 "
            " WHERE id=$1", r["id"], cov["coverage_pct"], cov["checkable"])
        if cov["coverage_pct"] < COVERAGE_FLOOR:
            below.append({"tier": r["tier"], "block": r["block"],
                          "citation_pct": cov["coverage_pct"],
                          "examples": cov["examples"]})
    log.info("market.remeasured", need_id=need_id, blocks=len(rows),
             changed=changed, below_floor=len(below))
    return {"need_id": need_id, "blocks": len(rows), "changed": changed,
            "below_floor": below, "floor": COVERAGE_FLOOR}
