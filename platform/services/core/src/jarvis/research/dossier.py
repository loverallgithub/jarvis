"""B2–B5 — gap analysis, willingness to pay, feasibility, synthesis.

Every factual statement produced here becomes a `claims` row with a
`NOT NULL evidence_id`. There is no path in this module that writes a claim
without first having fetched and hashed the thing it cites.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import httpx
import structlog

from .. import db
from . import evidence as ev
# Reused rather than reimplemented: it already recognises model refusals
# and unfinished-work markers, and a second weaker copy of that check is
# exactly how the duplicated _llm went wrong.
from ..forge.build import find_placeholders

log = structlog.get_logger("research.dossier")


def _text_of(payload: dict) -> Optional[str]:
    """Extract text from an Anthropic messages response.

    🔴 NOT `content[0]["text"]`.

    `claude-opus-5` returns **200** with a content array whose first block is
    not text — extended-thinking models emit a thinking block first. Indexing
    blindly raises `KeyError: 'text'`, which `_llm` caught and turned into
    `None`, which the caller read as "generation failed".

    The forge therefore produced **zero sections across all three tiers in 691
    seconds**, having paid for every one of those calls, and reported nothing
    more useful than "generation failed". The API was fine; the parser was wrong.

    Concatenate every block of type "text" and ignore the rest.
    """
    blocks = payload.get("content") or []
    parts = [b.get("text", "") for b in blocks
             if isinstance(b, dict) and b.get("type") == "text"]
    out = "\n".join(p for p in parts if p).strip()
    return out or None


# Prices in the wild: €297, $1,299.00, 49 EUR, £29/mo
_PRICE = re.compile(
    r"(?:(?P<sym>[$€£])\s?(?P<a>\d{1,3}(?:[,.]\d{3})*(?:\.\d{2})?)"
    r"|(?P<b>\d{1,3}(?:[,.]\d{3})*(?:\.\d{2})?)\s?(?P<cur>USD|EUR|GBP))")

_SYM = {"$": "USD", "€": "EUR", "£": "GBP"}


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

async def _llm(prompt: str, *, max_tokens: int = 900) -> Optional[str]:
    """Delegates to the ONE LLM path — the one with the OpenRouter fallback.

    🔴 THIS MODULE USED TO HOLD ITS OWN ANTHROPIC-ONLY COPY, AND THAT KILLED
    THE ENTIRE RESEARCH PHASE.

    When the Anthropic spend cap landed, a fallback to OpenRouter was added —
    to `forge/build.py` only. Nobody noticed that `research/dossier.py`
    maintained a SECOND implementation. Every research call has returned None
    ever since: gap analysis extracting nothing, willingness-to-pay finding
    nothing, and no error reaching the operator, because returning None on
    failure is this function's documented contract.

    Discovered 2026-08-09 when `research solution` produced zero queries and
    blamed missing positioning — positioning that was present. A silent None is
    indistinguishable from "the model had nothing to say", which is exactly why
    two copies of a failure path is worse than one.

    Keeps the `llm_model` parameter name this module has always used, so the
    research steps stay tunable independently of the forge.
    """
    from ..forge.build import _llm as _shared_llm
    return await _shared_llm(prompt, max_tokens=max_tokens,
                             model_param="llm_model")


def _json_block(text: str) -> Optional[list]:
    """Pull a JSON array out of a model reply. Returns None rather than
    guessing — a half-parsed extraction is worse than none."""
    if not text:
        return None
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
        return out if isinstance(out, list) else None
    except Exception:                                            # noqa: BLE001
        return None


async def _add_claim(need_id: int, text: str, evidence_id: int, kind: str,
                     confidence: float = 0.7,
                     run_id: Optional[int] = None) -> Optional[int]:
    """The ONLY way a claim gets written. `evidence_id` is mandatory in the
    signature as well as in the schema — belt and braces on the one rule that
    matters most."""
    if not evidence_id:
        raise ValueError("a claim requires an evidence_id — C4 is not optional")
    cid = await db.fetchval(
        """
        INSERT INTO claims (need_id, text, evidence_id, kind, confidence, run_id)
        VALUES ($1,$2,$3,$4,$5,$6) RETURNING id
        """, need_id, text[:2000], evidence_id, kind, confidence, run_id)
    return int(cid) if cid else None


# ---------------------------------------------------------------------------
# B2 — gap analysis
# ---------------------------------------------------------------------------

async def gap_analysis(need_id: int, run_id: Optional[int] = None) -> dict:
    """What is missing, badly done, or over-priced — every statement cited.

    Extraction runs per-evidence-row so the claim's citation is the page it
    came from, not a summary of several pages. A claim whose evidence is "some
    of the things I read" is not a citation.
    """
    need = await db.fetchrow("SELECT title, pain_statement FROM needs WHERE id=$1", need_id)
    # Only substantive pages, and at most N per domain: worldmetrics.org
    # produced the SAME three gaps twice from two captures of one site.
    # Repetition from one domain is redundancy, not corroboration.
    p = await ev.params()
    per_domain = int(p.get("max_claims_per_domain", 3))
    rows = await db.fetch(
        "SELECT id, url, title, body FROM evidence "
        "WHERE need_id=$1 AND live_at_capture AND substantive "
        "ORDER BY bytes DESC LIMIT 10", need_id)

    made = 0
    domain_counts: dict[str, int] = {}
    seen_texts: set[str] = set()
    for r in rows:
        dom = urlparse(r["url"] or "").netloc.lower()
        if domain_counts.get(dom, 0) >= per_domain:
            continue
        reply = await _llm(
            "You are analysing a competitor page to find GAPS in the market.\n\n"
            f"PROBLEM WE ARE INVESTIGATING: {need['title']}\n\n"
            f"PAGE TITLE: {r['title'] or 'untitled'}\nPAGE URL: {r['url']}\n"
            f"PAGE TEXT (truncated):\n{(r['body'] or '')[:4000]}\n\n"
            "List up to 3 specific gaps this page reveals — something the "
            "product does NOT do, does badly, or charges a lot for. Each gap "
            "must be supported by THIS page's text.\n\n"
            "🔴 STATE EACH GAP AS AN OBSERVATION, NOT AS AN ABSENCE.\n"
            "Write what the page DOES show and let the gap follow from it.\n"
            "  BAD:  'No mention of invoice OCR or document capture'\n"
            "  GOOD: 'The feature list covers approval routing and ERP sync, "
            "and stops there'\n"
            "  BAD:  'Only Tipalti is positioned for global payments'\n"
            "  GOOD: 'Tipalti is the one option shown at the $99+/mo tier for "
            "global payments'\n"
            "An absence claim cannot be checked against a page — if the text "
            "does not mention something, that is equally consistent with the "
            "page never mentioning it and with our excerpt missing it. Never "
            "write 'no mention of', 'lacks', 'does not discuss' or 'only X'.\n"
            'Reply with ONLY a JSON array of objects: '
            '[{"gap": "...", "confidence": 0.0-1.0}]\n'
            "If the page reveals no gap, reply with an empty array [].")
        items = _json_block(reply or "")
        if items is None:
            continue
        for it in items[:3]:
            g = str(it.get("gap", "")).strip()
            if len(g) < 20:
                continue
            key = re.sub(r"\W+", " ", g.lower())[:120]
            if key in seen_texts:            # the same gap, worded twice
                continue
            seen_texts.add(key)
            if domain_counts.get(dom, 0) >= per_domain:
                break
            await _add_claim(need_id, g, int(r["id"]), "gap",
                             float(it.get("confidence", 0.6)), run_id)
            domain_counts[dom] = domain_counts.get(dom, 0) + 1
            made += 1

    # B2 back-fills the score Phase A deliberately deferred.
    gap_score = None
    if made:
        # Scored on the number of DISTINCT DOMAINS that revealed a gap, not the
        # raw claim count. 21 claims from two sites scored a perfect 10.0 on the
        # first run, which is corroboration theatre — one site repeating itself
        # is one observation.
        gap_score = round(min(len(domain_counts) / 4.0, 1.0) * 10, 2)
        await db.execute("UPDATE needs SET gap = $2 WHERE id = $1", need_id, gap_score)

    log.info("research.gap_analysis", need_id=need_id, claims=made,
             domains=len(domain_counts), gap=gap_score)
    return {"claims": made, "gap": gap_score, "pages_analysed": len(rows),
            "gap_domains": len(domain_counts)}


# ---------------------------------------------------------------------------
# B3 — willingness to pay
# ---------------------------------------------------------------------------

@dataclass
class PriceObservation:
    amount: float
    currency: str
    evidence_id: int
    domain: str
    context: str


# 🔴 A CURRENCY FIGURE IS NOT A PRICE.
#
# Measured 2026-08-09 by the claim-level verifier, which was the first thing
# ever to check these individually:
#
#   "$33.4 Million Recovered"     -> stored as "USD 33.00"   (highradius)
#   "$5M+ in fraud identified"    -> stored as "USD 5.00"    (ramp)
#
# Two independent faults. The MAGNITUDE SUFFIX was dropped, turning 33.4
# million into 33; and nothing checked whether the surrounding words were about
# price at all. Both claims then fed `willingness_to_pay`, which anchors the
# tier ladder — so the live test ladder was priced partly off a fraud statistic
# and a recovery total.
#
# Rejecting is the right default here. A missed real price costs one data point;
# an invented one silently sets what the product sells for.
_MAGNITUDE = re.compile(r"^\s*(?:m|mm|bn?|k|million|billion|thousand|trillion)\b",
                        re.I)

# Words that mean the figure is an AGGREGATE, not a price a buyer pays.
_NOT_A_PRICE = re.compile(
    r"(?i)\b(recovered|raised|funding|valuation|revenue|arr|mrr\s+of|"
    r"processed|saved|savings|fraud|losses?|market size|worth|"
    r"transactions?|volume|invested|acquisition|round|damages|fined?|"
    r"penalt(?:y|ies)|settlement|billion|million)\b")

# Words that mean it probably IS a price. Not required — many real prices sit in
# a bare table cell — but enough to rescue a figure the anti-list would reject.
_IS_A_PRICE = re.compile(
    r"(?i)(/\s*(?:mo|month|yr|year|user|seat)\b|\bper\s+(?:month|year|user|seat)\b"
    r"|\bstarting at\b|\bfrom\s*[$€£]|\bprice[ds]?\b|\bpricing\b|\bcosts?\b"
    r"|\bplan\b|\bsubscription\b|\bbilled\b|\btier\b)")


def is_price_context(context: str, tail: str) -> bool:
    """True when a currency figure in `context` reads as a price.

    `tail` is the text immediately AFTER the number, used to spot a magnitude
    suffix — "$33.4 Million" must never become 33.
    """
    if _MAGNITUDE.match(tail or ""):
        return False
    if _IS_A_PRICE.search(context or ""):
        return True
    return not _NOT_A_PRICE.search(context or "")


async def willingness_to_pay(need_id: int, run_id: Optional[int] = None) -> dict:
    """Observed prices for adjacent solutions, each captured as evidence.

    🔴 **Never a regex over ONE page.** Pimlico priced €297 products from
    exactly that. This requires prices from at least **two distinct domains**
    before it will report anything, because one vendor's pricing page is a data
    point about that vendor, not about the market.
    """
    rows = await db.fetch(
        "SELECT id, url, body FROM evidence "
        "WHERE need_id=$1 AND live_at_capture AND body IS NOT NULL", need_id)

    obs: list[PriceObservation] = []
    for r in rows:
        domain = urlparse(r["url"] or "").netloc.lower()
        body = r["body"] or ""
        for m in _PRICE.finditer(body):
            raw = m.group("a") or m.group("b")
            cur = _SYM.get(m.group("sym") or "", m.group("cur") or "")
            if not raw or not cur:
                continue
            try:
                amount = float(raw.replace(",", ""))
            except ValueError:
                continue
            # Filter noise: years, tiny numbers, enterprise outliers.
            if amount < 5 or amount > 100_000 or 1990 <= amount <= 2100:
                continue
            start = max(0, m.start() - 60)
            context = body[start:m.end() + 60]
            if not is_price_context(context, body[m.end():m.end() + 14]):
                log.debug("research.price_rejected", url=r["url"][:90],
                          amount=amount, context=context.strip()[:110])
                continue
            obs.append(PriceObservation(amount, cur, int(r["id"]), domain,
                                        context))

    domains = {o.domain for o in obs}
    if len(domains) < 2:
        log.info("research.wtp_insufficient", need_id=need_id,
                 observations=len(obs), domains=len(domains))
        return {"observations": len(obs), "domains": len(domains),
                "sufficient": False,
                "reason": "fewer than 2 distinct domains — one vendor's pricing "
                          "page is a data point about that vendor, not the market"}

    amounts = sorted(o.amount for o in obs)
    mid = amounts[len(amounts) // 2]

    # One cited claim per distinct domain, so the pricing dossier is auditable
    # back to the pages it came from.
    seen: set[str] = set()
    for o in obs:
        if o.domain in seen:
            continue
        seen.add(o.domain)
        await _add_claim(
            need_id,
            f"{o.domain} shows a price of {o.currency} {o.amount:,.2f} "
            f"in context: …{o.context.strip()[:160]}…",
            o.evidence_id, "pricing", 0.6, run_id)

    body = json.dumps({"observations": len(obs), "domains": sorted(domains),
                       "min": amounts[0], "median": mid, "max": amounts[-1]},
                      default=str)
    await db.execute(
        """
        INSERT INTO dossiers (need_id, kind, body, run_id, evidence_count)
        VALUES ($1,'pricing',$2,$3,$4)
        ON CONFLICT (need_id, kind) DO UPDATE
          SET body = EXCLUDED.body, evidence_count = EXCLUDED.evidence_count
        """, need_id, body, run_id, len(obs))

    log.info("research.wtp", need_id=need_id, observations=len(obs),
             domains=len(domains), median=mid)
    return {"observations": len(obs), "domains": len(domains), "sufficient": True,
            "min": amounts[0], "median": mid, "max": amounts[-1]}


# ---------------------------------------------------------------------------
# B4 — feasibility, per tier
# ---------------------------------------------------------------------------

async def feasibility(need_id: int, run_id: Optional[int] = None) -> dict:
    """Can we build it with what we own? Resolved against the LIVE connector
    registry, not a wish list.

    · Roadmap      — always feasible. It is a document.
    · Instructions — feasible if every step can be described precisely.
    · Deployed     — feasible ONLY if the connectors it needs are `live` with a
                     passing contract test.

    This is what stops the Deployed tier over-promising. If it is infeasible,
    **the other two tiers still sell** — the ladder degrades gracefully, which
    is the entire reason it is a ladder.
    """
    rows = await db.fetch(
        "SELECT connector, state, last_contract_at FROM connector_health")
    live = {r["connector"] for r in rows
            if r["state"] == "live" and r["last_contract_at"] is not None}

    # What a deployed solution would actually need to exist.
    required = {"ghl_payments", "mailgun"}
    missing = sorted(required - live)

    out = {
        "roadmap": {"feasible": True,
                    "reason": "a roadmap is a document; always feasible"},
        "instructions": {"feasible": True,
                         "reason": "every step can be described; no live "
                                   "connector is required to write instructions"},
        "deployed": {
            "feasible": not missing,
            "reason": ("all required connectors are live with a passing contract test"
                       if not missing else
                       f"required connectors not live: {', '.join(missing)}. "
                       f"The Deployed tier is NOT offered for this solution; "
                       f"Roadmap and Instructions still sell."),
            "missing": missing,
        },
        "live_connectors": sorted(live),
    }
    await db.execute(
        """
        INSERT INTO dossiers (need_id, kind, body, run_id, feasibility)
        VALUES ($1,'competitive',$2,$3,$4::jsonb)
        ON CONFLICT (need_id, kind) DO UPDATE
          SET body = EXCLUDED.body, feasibility = EXCLUDED.feasibility
        """, need_id, json.dumps(out, default=str), run_id, json.dumps(out, default=str))
    log.info("research.feasibility", need_id=need_id,
             deployed=out["deployed"]["feasible"], missing=missing)
    return out


# ---------------------------------------------------------------------------
# B5 — synthesise
# ---------------------------------------------------------------------------

async def uncited_claims(need_id: int) -> int:
    """Must be zero. The schema makes it structurally impossible, and this
    counts anyway — a constraint nobody checks is a constraint that gets
    dropped by a later migration and missed."""
    return int(await db.fetchval(
        "SELECT count(*) FROM claims WHERE need_id = $1 AND evidence_id IS NULL",
        need_id) or 0)


async def synthesise(need_id: int, run_id: Optional[int] = None) -> dict:
    """B5 — assemble the Research Dossier and report against its acceptance."""
    st = await ev.stats(need_id)
    claims = await db.fetch(
        "SELECT kind, count(*) AS n FROM claims WHERE need_id=$1 GROUP BY kind",
        need_id)
    by_kind = {r["kind"]: int(r["n"]) for r in claims}
    uncited = await uncited_claims(need_id)

    need = await db.fetchrow(
        "SELECT title, pain_statement, audience, score, gap FROM needs WHERE id=$1",
        need_id)
    feas = await db.fetchval(
        "SELECT feasibility FROM dossiers WHERE need_id=$1 AND kind='competitive'",
        need_id)
    if isinstance(feas, str):
        feas = json.loads(feas)

    body = json.dumps({
        "need": dict(need) if need else {},
        "evidence": st,
        "claims_by_kind": by_kind,
        "uncited_claims": uncited,
        "feasibility": feas or {},
    }, default=str)

    await db.execute(
        """
        INSERT INTO dossiers (need_id, kind, body, run_id, evidence_count, claim_count,
                              feasibility)
        VALUES ($1,'research',$2,$3,$4,$5,$6::jsonb)
        ON CONFLICT (need_id, kind) DO UPDATE
          SET body = EXCLUDED.body, evidence_count = EXCLUDED.evidence_count,
              claim_count = EXCLUDED.claim_count, feasibility = EXCLUDED.feasibility
        """, need_id, body, run_id, int(st.get("live") or 0),
        sum(by_kind.values()), json.dumps(feas or {}, default=str))

    out = {
        "need_id": need_id,
        "evidence_total": int(st.get("total") or 0),
        "evidence_live": int(st.get("live") or 0),
        "evidence_usable": int(st.get("usable") or 0),
        "evidence_hashed": int(st.get("hashed") or 0),
        "domains": int(st.get("domains") or 0),
        "claims": sum(by_kind.values()),
        "claims_by_kind": by_kind,
        "uncited_claims": uncited,
        "gap": float(need["gap"]) if need and need["gap"] is not None else None,
        "deployed_feasible": bool((feas or {}).get("deployed", {}).get("feasible")),
    }
    log.info("research.synthesised", **{k: v for k, v in out.items()
                                        if k != "claims_by_kind"})
    return out


# ---------------------------------------------------------------------------
# SOLUTION RESEARCH — evidence for what the product CLAIMS, not what is missing
# ---------------------------------------------------------------------------
# 🔴 WHY THIS EXISTS.
#
# `gap_analysis` extracts what a page is MISSING, which is the right input for
# deciding what to build. Every claim on need 13 was therefore a gap claim, and
# phase F then tried to write sales copy from them.
#
# Measured 2026-08-09: `headline`, `subhead` and `objections` reached 100%
# citation coverage while `benefits` and `faq` sat at 0-50%, because the first
# three describe the PROBLEM (which gap claims evidence perfectly) and the last
# two describe the SOLUTION (which they cannot evidence at all). The copy was
# reduced to asserting things like "a written record gives you grounds to move"
# with nothing behind them — expertise the evidence base did not contain.
#
# Solution research asks the opposite question of a page: not "what is absent"
# but "what does this establish that a buyer could rely on".

# `fact` is the EXISTING kind for "a checkable statement", and the schema
# already constrains kind to fact/gap/pricing/competitor/feasibility. A first
# version invented "support" and the INSERT died on claims_kind_check — a new
# kind would have needed a migration to say something the vocabulary already
# said. "Cardholders have 120 days to raise a chargeback" is a fact.
SOLUTION_KIND = "fact"


# 🔴 EXTRACTION META-COMMENTARY IS NOT A CLAIM.
#
# `find_placeholders` catches refusals that OPEN with "I cannot" / "I'm sorry".
# Observed 2026-08-09, two variants walked straight past it and were stored as
# cited facts against ftc.gov:
#
#   "To provide accurate, checkable statements from this FTC announcement,
#    I would need the full text of the press release..."
#   "To extract checkable, useful statements about the rule itself, ..."
#
# The tell is not the opening phrase, it is that the sentence talks about the
# EXTRACTION TASK — the page, the provided text, what the model would need —
# rather than about the world. A claim citing ftc.gov that discusses what the
# model was given is worse than no claim: it looks authoritative.
#
# Kept local to extraction rather than added to find_placeholders, because
# "you would need to check your issuer's deadline" is legitimate prose in a
# BUILT ARTIFACT and must not start failing structural verification.
_EXTRACTION_META = re.compile(
    r"(?i)\b(?:i would need|i'd need|i am unable|i cannot|i can't|"
    r"cannot be extracted|no checkable statements|"
    r"the (?:page|provided|given) text|the text provided|the excerpt provided|"
    r"to (?:extract|provide) (?:accurate|checkable|useful|the requested))\b")


def _is_extraction_meta(text: str) -> bool:
    """True when the model is talking about the task instead of the world."""
    return bool(_EXTRACTION_META.search(text or ""))


async def solution_queries(need_id: int, n: int = 6) -> list[str]:
    """Search queries aimed at the REMEDY, derived from the positioning.

    Not from `needs.title`, which is a cluster label — on need 13 it is
    "payabl / automat / account", and the capture step's templates turn that
    into "payabl / automat / account software", a query that finds the vendor
    rather than the answer.
    """
    pos = await db.fetchrow(
        "SELECT pain_phrase, promise, audience FROM positioning WHERE need_id=$1",
        need_id)
    need = await db.fetchrow(
        "SELECT title, pain_statement FROM needs WHERE id=$1", need_id)
    if need is None:
        raise LookupError(f"no need {need_id}")

    context = (f"PAIN: {pos['pain_phrase']}\nPROMISE: {pos['promise']}\n"
               f"AUDIENCE: {pos['audience']}" if pos else
               f"PROBLEM: {need['title']}\n{need['pain_statement'] or ''}")

    reply = await _llm(
        "We are writing a practical guide that helps someone SOLVE this "
        "problem. Give search queries that would find AUTHORITATIVE, "
        "PROCEDURAL sources about the remedy — consumer rights, card-scheme "
        "rules, statutory notice periods, official vendor procedures, "
        "regulator guidance.\n\n"
        f"{context}\n\n"
        "Rules:\n"
        "- Ask about the REMEDY and the PROCEDURE, never about the vendor.\n"
        "- Prefer wording that surfaces primary sources over listicles.\n"
        "- One query per line, no numbering, no commentary.\n"
        f"- Exactly {n} queries.",
        max_tokens=400)

    lines = [ln.strip(" -•\t") for ln in (reply or "").splitlines()]
    # Drop markdown headings and commentary. Observed 2026-08-09: the model
    # emitted "# Search Queries for Subscription Access & Cancellation
    # Remedies" as its first line and it was searched as a query.
    out = [ln for ln in lines
           if 12 <= len(ln) <= 160 and not ln.startswith("#")
           and not ln.lower().startswith(("here are", "search quer"))][:n]
    log.info("research.solution_queries", need_id=need_id, produced=len(out))
    return out


async def support_analysis(need_id: int, run_id: Optional[int] = None) -> dict:
    """Extract facts that SUPPORT a remedy, one claim per evidence row.

    The mirror of `gap_analysis`. Same discipline: per-page extraction so the
    citation is the page the fact came from, a per-domain cap so one site cannot
    manufacture corroboration, and no claim without a verifiable statement.
    """
    p = await ev.params()
    per_domain = int(p.get("max_claims_per_domain", 3))
    rows = await db.fetch(
        "SELECT id, url, title, body FROM evidence "
        " WHERE need_id=$1 AND live_at_capture AND substantive "
        " ORDER BY bytes DESC LIMIT 12", need_id)

    made = 0
    domain_counts: dict[str, int] = {}
    seen: set[str] = set()
    for r in rows:
        dom = urlparse(r["url"] or "").netloc.lower()
        if domain_counts.get(dom, 0) >= per_domain:
            continue
        reply = await _llm(
            "Read this page and extract statements a PRACTICAL GUIDE could "
            "rely on and cite.\n\n"
            f"PAGE TITLE: {r['title'] or 'untitled'}\nPAGE URL: {r['url']}\n"
            f"PAGE TEXT (truncated):\n{(r['body'] or '')[:4000]}\n\n"
            "Extract up to 3 statements that are:\n"
            "- CHECKABLE against this page — a rule, a deadline, a procedure, "
            "a named right, a documented step\n"
            "- USEFUL to someone acting on the problem, not background colour\n"
            "- stated as the page states them, without exaggeration\n\n"
            "If the page supports nothing checkable, reply exactly: NONE\n"
            "One statement per line, no numbering.",
            max_tokens=600)

        if not reply or reply.strip().upper().startswith("NONE"):
            continue
        for line in reply.splitlines():
            text = line.strip(" -•\t")
            if len(text) < 30 or text.upper() == "NONE":
                continue
            # 🔴 A REFUSAL IS NOT A CLAIM. Observed 2026-08-09: the model
            # replied "I cannot extract checkable statements from this page
            # because the..." and that sentence was written to `claims` as a
            # cited fact. This is the exact Sintra/LinkedIn shape the platform
            # exists to prevent — an error string persisted as content, then
            # cited by a product. `find_placeholders` already recognises model
            # refusals and unfinished-work markers; reuse it rather than
            # inventing a second, weaker check.
            if find_placeholders(text) or _is_extraction_meta(text):
                log.info("research.refusal_discarded", need_id=need_id,
                         evidence_id=int(r["id"]), text=text[:90])
                continue
            key = text.lower()[:120]
            if key in seen:
                continue
            seen.add(key)
            cid = await _add_claim(need_id, text, int(r["id"]),
                                   SOLUTION_KIND, 0.7, run_id)
            if cid:
                made += 1
                domain_counts[dom] = domain_counts.get(dom, 0) + 1

    log.info("research.support_extracted", need_id=need_id, claims=made,
             pages=len(rows))
    return {"need_id": need_id, "claims": made, "pages_read": len(rows)}
