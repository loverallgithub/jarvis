"""E4 — verification, structural AND factual.

Pimlico only ever had the first. Its `verify` counted sections and word counts,
which a confidently-written fabrication passes effortlessly.

    STRUCTURAL  every planned section present · no placeholder text ·
                minimum length met · the file actually exists on disk
    FACTUAL     every claim the document cites is checked AGAINST ITS OWN
                EVIDENCE SNIPPET, and marked supported or not

A claim whose evidence does not support it is marked and **repaired**, not
retried — and repair branches on the DURABLE `repair_count`. Pimlico's guard
tested `attempts`, which `advance()` reset to 0 on every transition, so the
guard was always true and a section the model reliably answered "TBD" looped
for ever at full LLM cost.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

from .. import db
from .build import _llm, find_placeholders
from .plan import sections_for

log = structlog.get_logger("forge.verify")

EXCERPT_WIDTH = 2500
EXCERPT_HEAD = 400
# Measured on the four blocked claims (2026-08-08), scoring every window of each
# evidence body:
#
#   raw nav block                    0.08
#   article-TITLE list ("What Is X?  ~0.43   ← titles carry stopwords and '?',
#     Types, Definitions | ...")              which inflates a naive prose score
#   nav with prepositions             0.62   ("Solutions for Every Finance Need
#                                              By ERP SAP Integration ...")
#   real article prose               0.92-1.00
#
# and the bodies contain 35-70 windows at >= 0.85 each, so a high floor is not
# scarce. A first attempt used 0.35, which ADMITTED the 0.43 and 0.62 chrome —
# and chrome then won on coverage anyway, because a link list naming every ERP
# covers every term of a claim about ERP integration. The floor has to sit above
# the chrome, not merely above raw menus.
PROSE_FLOOR = 0.75

_WORD = re.compile(r"[a-z0-9][a-z0-9\-']{2,}")
# Phrases the claim quotes from its source, in straight or curly quotes. These
# are locators, not keywords — see `relevant_excerpt`.
_QUOTED = re.compile(r"['\"‘“]([^'\"’”]{6,120})['\"’”]")
_STOP = frozenset("""
the a an and or but for nor so yet of to in on at by with from as is are was
were be been being that this these those it its their there here what which
who whom how why not no than then them they you your our we us can could may
might will would shall should must have has had do does did done more most
other some such only own same too very just about into over under between
during without within across per also many much new use used using need needs
""".split())


def _terms(claim: str) -> list[str]:
    """Content words of the claim, with edge punctuation stripped.

    🔴 The strip is not cosmetic. `_WORD` permits `'` and `-` inside a token so
    that "doesn't" and "budget-conscious" survive — but it also permitted them
    at the END, so a claim quoting <'NA - Custom quote'> produced the token
    `quote'`, which can never match the word "quote" in any body text.
    Measured 2026-08-08 on claim 31: the window holding the actual pricing table
    lost coverage it should have won, the fact-checker was handed a window with
    the vendor names but not their prices, and reported — correctly — that the
    source "does not contain any pricing information". A quoted phrase in a
    claim is exactly the case where the words matter most, and it was the one
    case the tokeniser mangled.
    """
    out = []
    for w in _WORD.findall(claim.lower()):
        w = w.strip("'-")
        if len(w) >= 3 and w not in _STOP:
            out.append(w)
    return out


def prose_score(window: str) -> float:
    """0..1 — how much this window reads as ARTICLE rather than MENU CHROME.

    🔴 Why this exists. The first version of `relevant_excerpt` scored keyword
    coverage then density, and navigation menus are the densest keyword text on
    any page. A sidebar reading "Integrations · API · Pricing · ERP · Solutions"
    matches every term in a claim about ERP integration pricing, scores
    beautifully, and says nothing. Measured 2026-08-08: after the excerpt fix
    shipped, claims 32 and 34 failed with "incomplete footer/navigation content"
    and "primarily navigation menu and product listing content" — on bodies of
    35,505 and 60,000 characters where the article was definitely present. The
    fix had swapped one bad window for another.

    Two signals, because either alone is gameable:

      STOPWORD DENSITY  English prose runs ~25-40% stopwords ("the", "of",
                        "that"). Menu chrome is nearly all content words, so it
                        scores near zero. This is the strong signal.
      SENTENCE DENSITY  Prose ends sentences every ~12-25 words. A menu has
                        almost no terminal punctuation.

    Deliberately NOT a model call: the verifier's own input selection has to be
    checkable without another verifier.
    """
    words = _WORD.findall(window.lower())
    if len(words) < 25:
        return 0.0
    stop_ratio = sum(1 for w in words if w in _STOP) / len(words)
    sentences = len(re.findall(r"[.!?](?:\s|$)", window))
    sent_density = sentences / len(words)
    # Normalised against the low end of ordinary prose, then capped — being
    # MORE prose-like than an article is not better, so this must not run away.
    return 0.65 * min(1.0, stop_ratio / 0.22) + 0.35 * min(1.0, sent_density / 0.045)


def relevant_excerpt(body: str, claim: str, *, width: int = EXCERPT_WIDTH,
                     head: int = EXCERPT_HEAD) -> str:
    """The slice of the page the CLAIM is about — not whichever chars come first.

    🔴 Observed 2026-08-08 on need 13. This function did not exist; the query
    said `left(e.body, 2500)`. Claims 30 and 31 cite a page whose comparison
    table names Tipalti at character **3228** — 728 characters past the window.
    The verifier answered, correctly and uselessly, "the source excerpt does not
    contain any pricing information … or mentions of Stampli, Yooz, or Tipalti".
    Four claims were marked UNSUPPORTED and all three artifacts were withheld
    because of where a slice fell.

    That failure is invisible in the worst way: the rejection reason is a true
    statement about the text the model was shown, so it reads like a real
    content problem and sends you upstream to fix evidence capture that was
    never broken.

    The head of the page is always included — it carries the title and so the
    page's identity — followed by the highest-scoring window. Scoring is
    deterministic keyword overlap, NOT another LLM call: a verifier whose input
    selection is itself unverifiable is not a verifier.
    """
    body = body or ""
    if len(body) <= width:
        return body

    terms = _terms(claim)
    if not terms:
        return body[:width]

    low = body.lower()
    step = max(1, width // 4)

    # Score every window once: coverage, prose-likeness, density.
    cands = []
    for start in range(0, max(1, len(low) - width + step), step):
        window = low[start:start + width]
        distinct = sum(1 for t in set(terms) if t in window)
        hits = sum(window.count(t) for t in set(terms))
        prose = prose_score(body[start:start + width])
        cands.append((start, distinct, prose, min(hits, 900)))

    # 🔴 A PHRASE THE CLAIM QUOTES IS THE STRONGEST LOCATOR THERE IS.
    #
    # Keyword coverage is a proxy for "is the claim discussed here". When the
    # claim QUOTES the source — <multiple vendors show 'NA - Custom quote'> —
    # the proxy is unnecessary: find the quote. Measured 2026-08-08 on claim 31,
    # whose quoted phrase sits at offset 3079 while every top-coverage window
    # sat at 6875-8750; the fact-checker got the vendor names without their
    # prices and said, correctly, that it saw no pricing information.
    #
    # Checked before the heuristics because it is evidence, not a guess.
    anchors: list[int] = []
    for phrase in _QUOTED.findall(claim):
        p = phrase.strip().lower()
        if len(p) < 6:
            continue
        at = low.find(p)
        if at >= 0:
            anchors.append(max(0, at - width // 3))

    # 🔴 SEND BOTH WINDOWS. Choosing one was wrong in both directions.
    #
    # Coverage-only picked navigation, because a link list naming every ERP
    # covers every term of a claim about ERP integration (claims 33, 34).
    # Then a prose FLOOR fixed that and immediately broke the mirror case: a
    # PRICING TABLE and an INTEGRATIONS LIST are fragments, not sentences, so
    # they score as chrome — and for a claim about transparent pricing the table
    # IS the evidence. Measured 2026-08-08: the floor cleared claims 28/32/33 and
    # simultaneously regressed claim 31 ("does not contain any pricing
    # information, comparison tables") and left 34 failing for the same reason.
    #
    # The claim type decides which shape of text supports it, and we do not know
    # the claim type without asking a model — which is precisely what must not
    # be trusted to select the verifier's own input. So send the best PROSE
    # window and the best COVERAGE window, and let the fact-checker read both.
    # Two 2500-char windows is a bigger prompt, not a harder question.
    best_cov = max(cands, key=lambda c: (c[1], c[2], c[3]))
    prose_cands = [c for c in cands if c[2] >= PROSE_FLOOR]
    best_prose = (max(prose_cands, key=lambda c: (c[1], c[2], c[3]))
                  if prose_cands else None)

    if best_cov[1] <= 0:
        return body[:width]

    picks = list(dict.fromkeys(anchors))[:1] + [best_cov[0]]
    picks = [p for i, p in enumerate(picks)
             if all(abs(p - q) >= width // 2 for q in picks[:i])]
    if best_prose is not None and all(abs(best_prose[0] - p) >= width // 2
                                      for p in picks):
        # Only when it is genuinely a DIFFERENT part of the page — two
        # overlapping windows would spend tokens repeating themselves.
        picks.append(best_prose[0])
    picks.sort()

    if len(picks) == 1 and picks[0] <= head:
        return body[:width]

    parts = [body[:head].rstrip()]
    for start in picks:
        parts.append(body[start:start + width])
    return "\n[…]\n".join(parts)


@dataclass
class VerifyResult:
    artifact_id: int
    structural_ok: bool = False
    factual_ok: bool = False
    missing_sections: list[str] = field(default_factory=list)
    placeholders: list[str] = field(default_factory=list)
    thin_sections: list[str] = field(default_factory=list)
    file_present: bool = False
    claims_checked: int = 0
    claims_supported: int = 0
    unsupported: list[dict] = field(default_factory=list)
    # 🔴 Permanently 0 — `claims.evidence_id` is NOT NULL, so a claim row cannot
    # be uncited. Kept because the invariant is real and the field is persisted,
    # but it MEASURES NOTHING. `citation_*` below is the check that phrase was
    # always taken to mean.
    uncited_claims: int = 0
    citation_checkable: int = 0
    citation_cited: int = 0
    citation_pct: float = 100.0
    citation_misses: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.structural_ok and self.factual_ok

    def to_json(self) -> str:
        return json.dumps({
            "structural_ok": self.structural_ok, "factual_ok": self.factual_ok,
            "missing_sections": self.missing_sections,
            "placeholders": self.placeholders, "thin_sections": self.thin_sections,
            "file_present": self.file_present, "claims_checked": self.claims_checked,
            "claims_supported": self.claims_supported,
            "unsupported": self.unsupported[:10],
            "uncited_claims": self.uncited_claims,
            "citation_checkable": self.citation_checkable,
            "citation_cited": self.citation_cited,
            "citation_pct": self.citation_pct,
            "citation_misses": self.citation_misses}, default=str)


# 🔴 TOLERATE A MISSPELLED CITATION MARKER.
#
# Found 2026-08-09 in the need-13 artifacts: `[claip 33]` and `[claik 28]`.
# The model reached for a citation, typed it wrong by one letter, and the strict
# `\[claim N\]` pattern did not match — so two GENUINE citations were invisible
# to coverage and to `_cited_ids`, depressing the measured number and dropping
# two claims from the Sources block.
#
# Matching `cla` + two letters recovers the intent without becoming a wildcard:
# it still requires the bracket, the `cla` stem and a number, so ordinary prose
# in brackets cannot be mistaken for a citation.
_CITE = re.compile(r"\[cla[a-z]{2}\s+\d+\]", re.I)
_FENCE = re.compile(r"```.*?```", re.S)
_SOURCES_TAIL = re.compile(r"\n---\s*\n\s*##\s+Sources\b.*\Z", re.S | re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
# A number, a price, a percentage, or a word that asserts something measured.
# These are the sentences a reader is entitled to check.
_CHECKABLE = re.compile(
    r"(\$\s?\d|\d+\s?%|\b\d[\d,.]*\s*(?:hours?|days?|weeks?|months?|years?|"
    r"invoices?|users?|seats?|per\b)|\b\d{2,}\b|"
    # "3 of 5", "2 out of 3" — a ratio is a measurement even when both numbers
    # are single digits, which `\d{2,}` misses. Exposed 2026-08-09 by a test
    # asserting that removing the noun `vendors` lost nothing: it did lose this.
    r"\b\d+\s+(?:of|out of)\s+\d+\b|"
    r"\b(?:study|studies|survey|report(?:s|ed)?|research|according to|"
    r"benchmark|industry average|on average|median|typically|most\b|"
    r"majority)\b)", re.I)
#
# 🔴 `vendors?` AND `competitors?` WERE REMOVED on 2026-08-09, by operator
# decision, and the reasoning generalises.
#
# Everything left in this list is MEASUREMENT OR ATTRIBUTION vocabulary — a
# study, a median, "according to", "most". Those words signal that a sentence is
# reporting something someone measured, which is exactly what needs a citation.
#
# "vendors" is a domain NOUN. It signals subject matter, not measurement. It
# flagged table-of-contents lines that assert nothing at all:
#
#   "A decision tree for your next move — whether to pursue a chargeback,
#    switch vendors, or decide"                          <- trigger: 'vendors'
#   "Whether access is restored depends on your vendor's systems"
#                                                        <- trigger: 'vendor'
#
# Nothing is lost. A real claim ABOUT vendors always carries a genuine trigger
# alongside: "MOST vendors decline to publish a price", "40% of vendors hide
# pricing", "3 of 5 vendors require a sales call". The noun was never doing the
# work; the quantifier was.


# 🔴 A SENTENCE ABOUT THE PRODUCT IS NOT A CLAIM ABOUT THE WORLD.
#
# "The document is approximately 40-50 pages" and "typically 20-40 minutes" are
# checkable in principle and citable only against the artifact itself — never
# against the research. Counting them as UNCITED claims asked the copy to cite
# a source that could not exist, and six copy blocks sat below the floor on
# exactly this (2026-08-09). Operator decision: they do not count.
#
# Excluded from the DENOMINATOR rather than counted as cited. Counting them as
# cited would inflate coverage with sentences nobody checked; removing them
# says the honest thing, which is that they were never in scope.
#
# Deliberately narrow, so it cannot become a loophole. BOTH conditions must
# hold: the sentence must be about the deliverable, AND its numbers must be
# product metrics (pages, minutes, sections). So:
#
#   "The document is 40-50 pages"                    -> excluded
#   "The document proves 40% of vendors hide pricing" -> STILL COUNTED, because
#                                                        a percentage about
#                                                        vendors is a claim
#                                                        about the world
#   "Teams lose 12 hours a week"                      -> STILL COUNTED, no
#                                                        deliverable subject
_ABOUT_DELIVERABLE = re.compile(
    r"(?i)\b(?:this|the|these)\s+"
    r"(?:document|roadmap|manual|guide|playbook|instructions|pack|template|"
    r"checklist|worksheet|report|download|purchase)\b"
    r"|\byou(?:'ll| will)?\s+(?:receive|get|download)\b"
    r"|\bit\s+(?:contains|includes|covers|is structured)\b")

_PRODUCT_METRIC = re.compile(
    r"(?i)\b\d[\d,.–—-]*\s*"
    r"(?:pages?|minutes?|hours?|sections?|steps?|words?|chapters?|parts?)\b"
    r"|\bone sitting\b")


def is_product_self_description(sentence: str) -> bool:
    """True when a sentence describes the deliverable rather than the world."""
    s = sentence or ""
    return bool(_ABOUT_DELIVERABLE.search(s) and _PRODUCT_METRIC.search(s))


def citation_coverage(text: str) -> dict:
    """How much of the CHECKABLE prose actually cites something.

    🔴 THIS IS THE CHECK "zero uncited claims" WAS NEVER MAKING.
    `claims.evidence_id` is NOT NULL, so `uncited_claims` could only ever be 0 —
    a column constraint reported as a verification result. What the phrase
    sounds like it means is this: every assertion in the finished document that
    a reader could check carries a `[claim N]` marker. A sentence the generator
    wrote citing nothing produces NO claim row at all, so it was invisible to
    every count, and `structural()` checked sections, placeholders and length
    but never citation coverage.

    Deliberately conservative about what counts as checkable — numbers, money,
    percentages, and the vocabulary of measurement ("on average", "most",
    "according to"). Prose that frames or instructs ("Open the billing page")
    asserts nothing about the world and needs no citation; flagging it would
    bury the real misses in noise and train the reader to ignore the metric.

    Excluded from the scan: fenced code, headings, and the generated `## Sources`
    block — which is nothing but citations and would flatter the number.

    Returns counts plus up to five example misses, because a bare percentage
    tells you there is a problem and an example tells you what to fix.
    """
    body = _SOURCES_TAIL.sub("", text or "")
    body = _FENCE.sub(" ", body)

    checkable = cited = self_described = 0
    misses: list[str] = []
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or set(s) <= set("-|: "):
            continue
        for sent in _SENTENCE.split(s):
            sent = sent.strip()
            if len(sent) < 25 or not _CHECKABLE.search(sent):
                continue
            # Out of scope entirely — see is_product_self_description(). Not
            # counted as cited, because nobody checked it; not counted as
            # uncited, because no source could ever have.
            if is_product_self_description(sent):
                self_described += 1
                continue
            checkable += 1
            # Cited if the sentence carries a marker, or the line it sits on
            # does — a bullet often cites once and then elaborates.
            if _CITE.search(sent) or _CITE.search(s):
                cited += 1
            elif len(misses) < 5:
                misses.append(sent[:180])

    pct = (cited / checkable * 100.0) if checkable else 100.0
    return {"checkable": checkable, "cited": cited,
            "coverage_pct": round(pct, 1), "examples": misses,
            "self_described": self_described}


async def structural(artifact_id: int) -> VerifyResult:
    """Sections present, nothing placeholder-shaped, and the file really there."""
    row = await db.fetchrow(
        "SELECT id, need_id, tier, storage_uri, sections FROM artifacts WHERE id=$1",
        artifact_id)
    if row is None:
        raise LookupError(f"artifact {artifact_id} does not exist")

    res = VerifyResult(artifact_id=artifact_id)

    path = Path((row["storage_uri"] or "").replace("file://", ""))
    res.file_present = path.is_file() and path.stat().st_size > 0
    if not res.file_present:
        # An artifact whose file is missing cannot be delivered, so nothing
        # else about it matters.
        log.error("forge.verify_no_file", artifact_id=artifact_id, path=str(path))
        return res

    text = path.read_text(errors="replace")
    low = text.lower()

    # Citation coverage — MEASURED, NOT YET GATING.
    #
    # Deliberately does not feed `structural_ok`. Turning it into a gate would
    # withhold every artifact on a criterion nobody has seen a number for yet,
    # and "offerable" would change meaning silently. Measure on real documents
    # first, pick a threshold from what the data shows, then gate — in that
    # order. Making it gate is one line here once that decision is taken.
    cov = citation_coverage(text)
    res.citation_checkable = cov["checkable"]
    res.citation_cited = cov["cited"]
    res.citation_pct = cov["coverage_pct"]
    res.citation_misses = cov["examples"]
    if cov["checkable"] and cov["coverage_pct"] < 100.0:
        log.info("forge.citation_coverage", artifact_id=artifact_id,
                 cited=cov["cited"], checkable=cov["checkable"],
                 pct=cov["coverage_pct"])

    # Check against the CAPPED plan — the same object generation used.
    from ..research import evidence as ev
    p = await ev.params()
    plan = sections_for(row["tier"], max_sections=int(p.get("forge_max_sections", 8)))

    for s in plan:
        if f"## {s.heading}".lower() not in low:
            res.missing_sections.append(s.key)

    res.placeholders = find_placeholders(text)

    # Per-section length, measured between headings.
    chunks = re.split(r"\n##\s+", text)
    for s in plan:
        chunk = next((c for c in chunks if c.lower().startswith(s.heading.lower())), None)
        if chunk is not None and len(chunk.split()) < s.min_words:
            res.thin_sections.append(f"{s.key} ({len(chunk.split())} words)")

    res.structural_ok = not (res.missing_sections or res.placeholders
                             or res.thin_sections)
    return res


async def factual(artifact_id: int, res: VerifyResult,
                  verdicts: Optional[dict[int, tuple[bool, str]]] = None
                  ) -> VerifyResult:
    """Check each cited claim against the evidence snippet it cites.

    🔴 This is the check Pimlico never had. A structurally perfect document can
    be entirely fabricated; the only defence is asking, per claim, whether the
    source actually says it.

    ── `verdicts` — CHECK EACH CLAIM ONCE PER RUN ─────────────────────────────
    Claims belong to the NEED, not to one artifact: all three tiers of need 13
    cite the SAME 14 claims (`artifact_claims`: 42 rows, 14 distinct). Verifying
    per artifact therefore fact-checked every claim three times, wrote all three
    answers to the same `claims` row, and let the last one win.

    Measured 2026-08-08, one run, identical inputs: the three passes returned 3,
    3 and 2 unsupported over the same 14 claims, and claim 33 flipped from
    supported to unsupported with nothing changed but the pass. So `offerable`
    was partly a coin flip, at three times the LLM cost, and the artifact flags
    written earlier in the loop described verdicts the later passes overwrote.

    Pass a dict and it is used as a per-run memo: a claim already decided is
    reused rather than re-asked. The verdict for a claim is then a property of
    the claim, which is what it always was.
    """
    memo = verdicts if verdicts is not None else {}
    claims = await db.fetch(
        """
        SELECT c.id, c.text, e.url, e.body AS body
          FROM artifact_claims ac
          JOIN claims c ON c.id = ac.claim_id
          JOIN evidence e ON e.id = c.evidence_id
         WHERE ac.artifact_id = $1
        """, artifact_id)

    res.uncited_claims = int(await db.fetchval(
        "SELECT count(*) FROM artifact_claims ac JOIN claims c ON c.id = ac.claim_id "
        "WHERE ac.artifact_id = $1 AND c.evidence_id IS NULL", artifact_id) or 0)

    from ..research import evidence as _ev_mod
    _params = await _ev_mod.params()

    if not claims:
        # ⚠️ An artifact with NO citations is not "verified", it is UNVERIFIED.
        # Treating it as factually fine is exactly how two artifacts came to be
        # marked OFFERABLE after their claims were stolen by the next package.
        res.factual_ok = False
        log.warning("forge.verify_no_claims", artifact_id=artifact_id,
                    hint="an artifact citing nothing cannot be fact-checked; "
                         "it is unverified, not verified")
        return res

    for c in claims:
        res.claims_checked += 1

        if c["id"] in memo:
            # Already decided this run. Reused, not re-asked — and NOT re-written
            # to the row, so the stored verdict stays the one that was measured.
            supported, why = memo[c["id"]]
            if supported:
                res.claims_supported += 1
            else:
                res.unsupported.append({"claim_id": c["id"], "why": why,
                                        "text": c["text"][:160]})
            continue

        # An ABSENCE is decidable for free against the full body. Try that
        # first: it is deterministic, and the model's answer on this claim
        # shape has been provably unstable run to run.
        det = verify_absence(c["text"] or "", c["body"] or "") \
            if is_absence_claim(c["text"] or "") else None
        if det is not None:
            supported, why = det, (
                "topic absent from the full source body — gap confirmed "
                "deterministically" if det else
                "the source DOES discuss this topic — the absence claim is false "
                "(checked against the full body, not an excerpt)")
        else:
            snippet = relevant_excerpt(c["body"] or "", c["text"] or "")
            reply = await _llm(
                "You are fact-checking ONE claim against the source it cites.\n\n"
                f"CLAIM: {c['text']}\n\n"
                f"SOURCE URL: {c['url']}\n"
                f"SOURCE TEXT (excerpt):\n{snippet}\n\n"
                "Does the SOURCE TEXT support the CLAIM? Be strict: 'supported' "
                "means a reader of the source would agree the claim follows from "
                "it. Absence of contradiction is NOT support.\n"
                'Reply with ONLY JSON: {"supported": true|false, "why": "one sentence"}',
                # 🔴 Its OWN model and budget. Running this on opus with 200
                # tokens meant the thinking block consumed the whole budget, no
                # text block was ever emitted, and 9 checks recorded "did not
                # return a usable answer" — the verifier breaking, silently, and
                # being counted as claims failing.
                max_tokens=int(_params.get("verify_max_tokens", 400)),
                model_param="verify_model")

            supported, why = None, "verification did not return a usable answer"
            if reply:
                m = re.search(r"\{.*\}", reply, re.S)
                if m:
                    try:
                        d = json.loads(m.group(0))
                        supported = bool(d.get("supported"))
                        why = str(d.get("why", ""))[:300]
                    except Exception:                            # noqa: BLE001
                        pass

            # 🔴 An unverifiable claim is NOT a supported claim. Defaulting to
            # true on a failed check is how a verifier comes to approve
            # everything it was built to catch.
            if supported is None:
                supported = False

        await db.execute(
            "UPDATE claims SET supported = $2, support_reason = $3, verified_at = now() "
            "WHERE id = $1", c["id"], supported, why)
        memo[c["id"]] = (supported, why)
        if supported:
            res.claims_supported += 1
        else:
            res.unsupported.append({"claim_id": c["id"], "why": why,
                                    "text": c["text"][:160]})

    res.factual_ok = (not res.unsupported) and res.uncited_claims == 0
    if res.unsupported:
        log.warning("forge.unsupported_claims", artifact_id=artifact_id,
                    count=len(res.unsupported))
    return res


async def verify(artifact_id: int,
                 verdicts: Optional[dict[int, tuple[bool, str]]] = None
                 ) -> VerifyResult:
    """Verify one artifact. Pass `verdicts` to share claim decisions across the
    artifacts of one need — see `factual()` for why that matters."""
    res = await structural(artifact_id)
    if res.file_present:
        res = await factual(artifact_id, res, verdicts)

    await db.execute(
        """
        UPDATE artifacts SET structural_ok = $2, factual_ok = $3,
               verify_detail = $4::jsonb, offerable = $5
         WHERE id = $1
        """, artifact_id, res.structural_ok, res.factual_ok, res.to_json(), res.ok)

    log.info("forge.verified", artifact_id=artifact_id, ok=res.ok,
             structural=res.structural_ok, factual=res.factual_ok,
             claims=res.claims_checked, supported=res.claims_supported,
             unsupported=len(res.unsupported))
    return res


async def repairable(run_id: int, step_id: str = "forge.verify") -> int:
    """Remaining repair budget, from the DURABLE counter."""
    from ..runtime import engine
    return await engine.repair_budget_remaining(run_id, step_id)


async def verify_claims(need_id: int, only_unverified: bool = True) -> dict:
    """Fact-check claims that no artifact cites yet.

    🔴 WHY THIS IS SEPARATE FROM `verify()`.

    `factual()` walks `artifact_claims`, so it can only check claims a packaged
    artifact already cites. Solution research produces claims BEFORE anything
    cites them — and `market.copy` draws only on `supported IS TRUE`, so a
    freshly extracted claim with `supported = NULL` is invisible to the copy
    generator.

    Measured 2026-08-09: eight solution claims were extracted, `forge reverify`
    checked the same fourteen artifact claims as before, and the eight stayed
    NULL. The research ran, the evidence landed, and nothing downstream could
    see any of it. A claim nobody has checked is not usable as evidence, and a
    claim nobody CAN check is worse — it sits in the table looking like one.

    Same excerpt selection and the same strictness as the artifact path; the
    only difference is which claims it walks.
    """
    where = "AND c.supported IS NULL" if only_unverified else ""
    claims = await db.fetch(
        f"""
        SELECT c.id, c.text, e.url, e.body AS body
          FROM claims c JOIN evidence e ON e.id = c.evidence_id
         WHERE c.need_id = $1 {where}
         ORDER BY c.id
        """, need_id)
    if not claims:
        return {"need_id": need_id, "checked": 0, "supported": 0,
                "unsupported": 0, "detail": []}

    from ..research import evidence as _ev_mod
    params = await _ev_mod.params()

    supported_n = 0
    detail: list[dict] = []
    for c in claims:
        # An ABSENCE is decidable for free against the full body. Try that
        # first: it is deterministic, and the model's answer on this claim
        # shape has been provably unstable run to run.
        det = verify_absence(c["text"] or "", c["body"] or "") \
            if is_absence_claim(c["text"] or "") else None
        if det is not None:
            supported, why = det, (
                "topic absent from the full source body — gap confirmed "
                "deterministically" if det else
                "the source DOES discuss this topic — the absence claim is false "
                "(checked against the full body, not an excerpt)")
        else:
            snippet = relevant_excerpt(c["body"] or "", c["text"] or "")
            reply = await _llm(
                "You are fact-checking ONE claim against the source it cites.\n\n"
                f"CLAIM: {c['text']}\n\n"
                f"SOURCE URL: {c['url']}\n"
                f"SOURCE TEXT (excerpt):\n{snippet}\n\n"
                "Does the SOURCE TEXT support the CLAIM? Be strict: 'supported' "
                "means a reader of the source would agree the claim follows from "
                "it. Absence of contradiction is NOT support.\n"
                'Reply with ONLY JSON: {"supported": true|false, "why": "one sentence"}',
                max_tokens=int(params.get("verify_max_tokens", 400)),
                model_param="verify_model")

            supported, why = None, "verification did not return a usable answer"
            if reply:
                m = re.search(r"\{.*\}", reply, re.S)
                if m:
                    try:
                        d = json.loads(m.group(0))
                        supported = bool(d.get("supported"))
                        why = str(d.get("why", ""))[:300]
                    except Exception:                                # noqa: BLE001
                        pass
            if supported is None:
                supported = False        # unverifiable is never supported

        await db.execute(
            "UPDATE claims SET supported=$2, support_reason=$3, verified_at=now() "
            " WHERE id=$1", c["id"], supported, why)
        supported_n += 1 if supported else 0
        detail.append({"claim_id": int(c["id"]), "supported": supported,
                       "why": why, "text": (c["text"] or "")[:120]})

    log.info("forge.claims_verified", need_id=need_id, checked=len(claims),
             supported=supported_n, unsupported=len(claims) - supported_n)
    return {"need_id": need_id, "checked": len(claims), "supported": supported_n,
            "unsupported": len(claims) - supported_n, "detail": detail}


# ── deterministic verification of ABSENCE claims ───────────────────────────
#
# 🔴 YOU CANNOT CONFIRM AN ABSENCE FROM A SAMPLE.
#
# `gap_analysis` produces claims of the form "No mention of X". Fact-checking
# those against a 2,500-character excerpt asks the model whether something is
# absent from a page it has only partly seen — and if the excerpt does not
# mention X, that is equally consistent with "the page never does" and "the
# excerpt missed it". The verifier has been GUESSING, and which way it guesses
# changes run to run.
#
# Measured across 2026-08-08/09 on need 13, the same claim set produced 3, 3, 2,
# 4, 2 and 1 unsupported on six identical runs, with the identity of the failing
# claims changing each time. Claims 30, 34 and 36 were each hand-fixed after
# failing; 36 had been PASSING moments earlier with no input change.
#
# An absence is decidable against the FULL BODY, for free, with no model and no
# variance: look for the topic. Present means the claim is false. Absent means
# the gap is real.
_ABSENCE = re.compile(
    r"(?i)^\s*(?:no\s+(?:mention|discussion|reference|specific\s+guidance|detail)"
    r"|lacks?\b|lack\s+of\b|limited\s+(?:detail|transparency|information)"
    r"|the\s+page\s+(?:lacks|does\s+not|omits)|(?:does|do)\s+not\s+(?:mention|discuss|provide|cover))")

# The topic is what sits between the absence phrase and the first separator —
# "No mention of invoice OCR or document capture capabilities - the page ..."
_TOPIC_TAIL = re.compile(r"\s+[-–—]\s+|\s*\.\s|,\s+(?:the|which|leaving)\b")


# Words that carry no topic. Generic verbs are the dangerous ones: "provide",
# "discuss" and "mention" appear in nearly all prose, so treating them as part
# of the topic makes almost any page look like it covers almost anything.
_TOPIC_NOISE = frozenset("""
page pages capabilities capability information specific detail details options
content section sections provide provides provided discuss discusses discussed
mention mentions mentioned cover covers covered include includes included offer
offers offered show shows shown give gives given available specifics
""".split())


def is_absence_claim(text: str) -> bool:
    return bool(_ABSENCE.match(text or ""))


def _topic_terms(text: str) -> list[str]:
    head = _TOPIC_TAIL.split(text or "", 1)[0]
    head = _ABSENCE.sub("", head, count=1)
    terms = []
    for w in _WORD.findall(head.lower()):
        w = w.strip("'-")
        if len(w) >= 4 and w not in _STOP and w not in _TOPIC_NOISE:
            terms.append(w)
    return sorted(set(terms))


def verify_absence(text: str, body: str) -> Optional[bool]:
    """True if the gap is real, False if the page discusses it, None if undecidable.

    Matching is by PREFIX so "capture" finds "capturing" — the exact miss that
    let claim 36 assert "no mention of document capture" against a page saying
    "capturing invoices".

    Returns None rather than guessing when there is too little to go on; the
    caller then falls back to the model. A deterministic check that answers
    confidently on thin input is just a slower guess.
    """
    terms = _topic_terms(text)
    if len(terms) < 2 or not body:
        return None
    low = body.lower()
    present = sum(1 for t in terms if (t[:6] if len(t) > 6 else t) in low)

    # 🔴 REFUTING TAKES UNANIMITY; CONFIRMING TAKES SILENCE. Anything between
    # is handed to the model.
    #
    # A first version refuted on a MAJORITY and wrongly killed 9 of 14 claims in
    # one run. "The page does not provide specific pricing information or cost
    # comparison" yielded the terms {provide, cost, comparison, pricing};
    # "provide" and "cost" occur in almost any prose, so two incidental hits out
    # of four refuted a gap that was real — the page contains neither "pricing"
    # nor "price".
    #
    # The asymmetry is deliberate. A false REFUTATION deletes a true claim from
    # a product; a false CONFIRMATION lets a wrong claim through to the model,
    # which is the check we already had. Err toward asking.
    # 🔴 CONFIRM ONLY. THIS CHECK NO LONGER REFUTES.
    #
    # Refuting by term presence is the wrong instrument, and two rounds of
    # tuning did not change that. Measured 2026-08-09:
    #
    #   majority rule   -> 9 of 14 claims wrongly refuted
    #   unanimity rule  -> 3 wrongly refuted, including claim 31, "Lack of
    #                      TRANSPARENT pricing — vendors show 'NA - Custom
    #                      quote'". The words "transparent" and "pricing" are
    #                      both on the page, so presence refutes it; but the
    #                      claim is about the QUALITY of what is shown, and its
    #                      own evidence IS that table. The model had it right.
    #
    # Presence of a word is not coverage of an assertion. A claim about how
    # something is done cannot be settled by looking for whether it is
    # mentioned, so refutation is left to the model, which can read.
    #
    # The CONFIRMING half is sound and kept: if not one term of the topic
    # appears anywhere in the full body, the page does not discuss it, and that
    # is exactly the judgement an excerpt cannot make.
    if present == 0:
        return True        # nothing of the topic is on the page — gap is real
    return None            # anything else is the model's call
