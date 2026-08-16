"""Generate, package and verify the tier artifacts.

────────────────────────────────────────────────────────────────────────────
ONE LLM CALL PER SECTION, NEVER ONE PER PRODUCT
────────────────────────────────────────────────────────────────────────────
This is the one thing Pimlico got right and it is worth preserving: per-section
generation is what produced its genuine 24–28k-word depth. A single call for a
whole product produces a brochure.

Each call is given only the claims relevant to that section, and every claim
carries its evidence URL and hash — so the model is writing FROM cited material
rather than from recall, and the citation list at the end is not decoration.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx
import structlog

from .. import db
from ..config import settings
from ..research import evidence as ev
from .plan import Section, sections_for, tiers_up_to

log = structlog.get_logger("forge.build")


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


ARTIFACT_DIR = Path(os.environ.get("JPD_ARTIFACT_DIR", "/app/data/artifacts"))
DRAFT_DIR = ARTIFACT_DIR / "drafts"


def _draft_path(need_id: int, tier: str) -> Path:
    return DRAFT_DIR / f"need-{need_id}-{tier}.json"


def save_draft(need_id: int, tier: str, sections: list["GeneratedSection"]) -> Path:
    """Persist generated sections the moment they exist.

    🔴 Generation is the expensive step — 696 seconds and real tokens for three
    tiers. Holding the output only in `ctx.data` meant a failure in the NEXT
    step lost all of it, and re-running hit the idempotency cache (generate had
    "succeeded") and found nothing to package. Paid-for work must survive the
    step that produced it.
    """
    DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    path = _draft_path(need_id, tier)
    path.write_text(json.dumps(
        [{"key": s.key, "heading": s.heading, "text": s.text,
          "words": s.words, "claim_ids": s.claim_ids} for s in sections],
        ensure_ascii=False))
    return path


def load_draft(need_id: int, tier: str) -> list["GeneratedSection"]:
    path = _draft_path(need_id, tier)
    if not path.is_file():
        return []
    try:
        return [GeneratedSection(**d) for d in json.loads(path.read_text())]
    except Exception as e:                                       # noqa: BLE001
        log.warning("forge.draft_unreadable", path=str(path), error=str(e)[:150])
        return []

# Anything the verifier must never find in a FINISHED artifact.
#
# 🔴 These are REGEXES, not substrings, and that distinction cost two good
# artifacts. Naive substring matching withheld both the Instructions and
# Deployed tiers over:
#
#   · `"custom quote" placeholders`  — legitimate prose ABOUT vendor pricing
#   · `"...I cannot access the account and the merchant has not responded"`
#                                    — inside a script the BUYER reads to their bank
#
# A verifier that withholds good products is as damaging as one that passes bad
# ones: both destroy trust in the gate, and the second failure mode is the one
# people "fix" by disabling it. Each pattern below is anchored to how the string
# appears in UNFINISHED work — bracketed, shouted, or at the start of a line.
_PLACEHOLDER_PATTERNS = (
    # NOTE the case flags. `(?i)` where the phrase is unfinished work in any
    # casing; case-SENSITIVE where the shouting is the signal (TBD, PLACEHOLDER,
    # XXXX) and the lowercase form appears in legitimate prose.
    (r"(?i)lorem ipsum", "lorem ipsum"),
    (r"\bTBD\b", "TBD"),
    (r"(?i)\bto be determined\b", "to be determined"),
    (r"(?im)^\s*TODO\b", "TODO line"),
    # 🔴 `your` was REMOVED from this alternation on 2026-08-09, by operator
    # decision. It conflated two different things that happen to share a
    # notation:
    #
    #   [your billing descriptor]  a FIELD THE BUYER FILLS IN. The instructions
    #                             and deployed tiers hand the buyer ready-made
    #                             emails to send their vendor; a slot for their
    #                             own reference is the point of the template,
    #                             not an omission.
    #   [insert vendor name]      WORK THE AUTHOR DID NOT FINISH.
    #
    # Both artifacts #7 and #8 were withheld from sale solely because of
    # `[your billing descriptor]` and `[your account email]`. The rule was
    # rejecting the deliverable for doing its job.
    #
    # Residual risk, accepted knowingly: lorem of the form "[your name here]"
    # would now pass. No current artifact contains anything of that shape — the
    # only `[your ...]` tokens across all three are billing descriptor, account
    # email and billing email, all genuine template fields.
    (r"(?i)\[(?:insert|placeholder|xxx+)\b", "bracketed placeholder"),
    # 🔴 Found in SHIPPABLE SALES COPY on 2026-08-09, five markers across three
    # blocks, every one of which passed the coverage gate:
    #
    #   [Price would go here]
    #   [claim needed: what documentation triggers cancellation rights]
    #   [X business days — needs data]
    #
    # An artifact carrying `[insert vendor name]` is withheld from sale; copy
    # carrying `[Price would go here]` was shipping. These are the author
    # talking to themselves, and on a sales page a buyer reads it.
    (r"(?i)\[[^\]]*\b(?:claim|citation|source|data|figure)s?\s+needed\b",
     "claim-needed marker"),
    (r"(?i)\[[^\]]*\bwould go here\b", "would-go-here marker"),
    (r"(?i)\[[^\]]*\bneeds?\s+(?:data|verif|checking|confirm)", "needs-data marker"),
    # 🔴 The SHOUTED form, found on 2026-08-09 in a sales page that had just
    # been marked PUBLISHABLE: `[NEEDS PRICING]` (x2), `[NEEDS DETAIL]`,
    # `[NEEDS TIMELINE DATA]`, `[NEEDS DETAIL ON SELF-SERVICE FEATURE SET]`.
    #
    # The lowercase rule above needs the noun to follow immediately
    # (`needs data`), so `NEEDS TIMELINE DATA` slipped between the words. Kept
    # case-SENSITIVE and bracket-anchored on purpose: shouting inside brackets
    # is the author talking to themselves, while lowercase "needs pricing"
    # is ordinary prose a buyer might legitimately read.
    (r"\[NEEDS\b[^\]]*\]", "NEEDS marker"),
    (r"(?i)<(?:insert|placeholder|xxx+)\b", "angle-bracket placeholder"),
    (r"\bPLACEHOLDER\b", "shouted PLACEHOLDER"),
    (r"\bX{4,}\b", "XXXX filler"),
    (r"(?i)\bcoming soon\b", "coming soon"),
    (r"(?i)as an AI language model", "model preamble"),
    # Model refusals start a reply; they do not appear mid-sentence in prose.
    (r"(?im)^\s*(?:I'm sorry|I cannot|I can't|I am unable)\b", "model refusal"),
    (r"(?i)\[Automation failed", "automation failure text"),
)


def find_placeholders(text: str) -> list[str]:
    """Return the labels of any unfinished-work markers present."""
    import re as _re
    return [label for pattern, label in _PLACEHOLDER_PATTERNS
            if _re.search(pattern, text)]


# Kept for callers that only need the labels.
_PLACEHOLDER_MARKERS = tuple(label for _, label in _PLACEHOLDER_PATTERNS)


@dataclass
class GeneratedSection:
    key: str
    heading: str
    text: str
    words: int
    claim_ids: list[int] = field(default_factory=list)


def _text_of_openai(payload: dict) -> Optional[str]:
    """Extract text from an OpenAI-shaped (OpenRouter) response.

    A different wire shape from Anthropic's — `choices[].message.content` is a
    plain string, with no block types to filter. Parsed in its own function
    rather than by branching inside `_llm`, so neither parser can quietly be
    handed the other's payload.
    """
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return None
    msg = choices[0].get("message") or {}
    text = (msg.get("content") or "").strip()
    return text or None


_OR_MODELS: set[str] = set()


async def _openrouter_models(c: httpx.AsyncClient) -> set[str]:
    global _OR_MODELS
    if not _OR_MODELS:
        r = await c.get("https://openrouter.ai/api/v1/models", timeout=30)
        if r.status_code == 200:
            _OR_MODELS = {m["id"] for m in (r.json() or {}).get("data", [])}
    return _OR_MODELS


async def _resolve_openrouter_model(c: httpx.AsyncClient, model: str) -> Optional[str]:
    """Map an Anthropic-native model id to the id OpenRouter actually serves.

    🔴 THE TWO PROVIDERS DO NOT SHARE AN ID SPACE, and assuming they did cost a
    whole forge run. `claude-opus-5` is identical on both, so a plain
    `anthropic/` prefix looked correct — then `verify_model`
    (`claude-haiku-4-5-20251001`, Anthropic's dated id) became
    `anthropic/claude-haiku-4-5-20251001` and returned **400 "not a valid model
    ID" forty-two times**. Generation succeeded; every factual check failed;
    nothing became offerable.

    So the id is **resolved against the live `/api/v1/models` list**, never
    assumed. The two normalisations below are applied only as candidates and
    only ever accepted if the served list confirms them:

        claude-haiku-4-5-20251001  ->  claude-haiku-4-5   (drop the date)
                                   ->  claude-haiku-4.5   (n-n -> n.n)

    If nothing matches we return None and log the served anthropic ids, because
    the honest failure is "this model is not available here", not a guess.
    """
    if "/" in model:
        return model
    served = await _openrouter_models(c)
    if not served:
        return f"anthropic/{model}"          # list unavailable; try the plain form

    base = re.sub(r"-\d{8}$", "", model)     # drop a trailing YYYYMMDD
    candidates = [model, base, re.sub(r"-(\d)-(\d)", r"-\1.\2", base)]
    for cand in candidates:
        routed = f"anthropic/{cand}"
        if routed in served:
            if routed != f"anthropic/{model}":
                log.info("forge.openrouter_model_resolved",
                         configured=model, routed=routed)
            return routed

    log.error("forge.openrouter_model_unavailable", configured=model,
              tried=[f"anthropic/{c}" for c in candidates],
              served=sorted(m for m in served if m.startswith("anthropic/"))[:12])
    return None


async def _via_openrouter(prompt: str, model: str, max_tokens: int) -> Optional[str]:
    """Second route for the same model, used only after the first one fails.

    OpenRouter namespaces Anthropic models as `anthropic/<id>`; the ids in
    `research_params` are bare, so they are prefixed here rather than being
    duplicated in the database. Verified 2026-08-08 that `anthropic/claude-opus-5`,
    `anthropic/claude-haiku-4.5` and `anthropic/claude-sonnet-5` are all served.

    🔴 The model id is still never guessed. A wrong id returns
    `404 "No endpoints found for ..."` — which reads exactly like a dead key,
    the same trap as the three guessed Anthropic model names. If this 404s,
    query `https://openrouter.ai/api/v1/models` before blaming the credential.
    """
    key = os.environ.get("JPD_OPENROUTER_KEY", "")
    if not key or key == "CHANGE_ME":
        return None
    try:
        async with httpx.AsyncClient(timeout=180) as c:
            routed = await _resolve_openrouter_model(c, model)
            if routed is None:
                return None
            r = await c.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}",
                         "content-type": "application/json"},
                json={"model": routed, "max_tokens": max_tokens,
                      "messages": [{"role": "user", "content": prompt}]})
        if r.status_code != 200:
            # r.text, never the request — the key is an Authorization header and
            # must not reach a log line.
            log.warning("forge.llm_fallback_failed", route="openrouter",
                        model=routed, status=r.status_code, detail=r.text[:200])
            return None
        body = r.json()
        text = _text_of_openai(body)
        if text is None:
            # 🔴 Truncation and refusal are different failures with different
            # fixes, and both arrive as 200-with-no-content. An extended-thinking
            # model spends `max_tokens` on reasoning FIRST, so too small a budget
            # yields finish_reason="length" and an empty content field — a
            # BUDGET problem that reads exactly like a broken provider. Observed
            # 2026-08-08: opus-5 at max_tokens=16 returns nothing, at 64 returns
            # the answer.
            finish = ((body.get("choices") or [{}])[0] or {}).get("finish_reason")
            log.warning("forge.llm_fallback_no_text", route="openrouter",
                        model=routed, finish_reason=finish,
                        hint=("max_tokens too small — reasoning consumed the budget"
                              if finish == "length" else "empty content"))
            return None
        log.info("forge.llm_served_by_fallback", route="openrouter", model=routed,
                 usage=(r.json().get("usage") or {}).get("total_tokens"))
        return text
    except Exception as e:                                       # noqa: BLE001
        log.warning("forge.llm_fallback_error", route="openrouter", error=str(e)[:200])
        return None


async def _llm(prompt: str, *, max_tokens: int = 2000,
               model_param: str = "forge_model") -> Optional[str]:
    """Returns None on any failure — never an error string.

    A failure that returns text would be the Sintra shape: indistinguishable
    from real output at every layer downstream, and eventually published.

    **Two routes, tried in order: `api.anthropic.com`, then OpenRouter.**
    Added 2026-08-08 because the Anthropic key hit a self-imposed spend cap
    mid-forge and *"an unverifiable claim is not a verified claim"* left three
    finished artifacts unofferable with no way to complete the factual pass
    until the cap reset. One provider was a single point of failure on the only
    step that costs real money.

    ⚠️ The fallback changes WHO SERVES the call, never what counts as success.
    A `None` from both routes is still a failure, the verifier still refuses to
    mark unchecked claims supported, and nothing here weakens a gate.
    """
    p = await ev.params()
    model = p.get(model_param, "claude-opus-5")

    key = os.environ.get("JPD_ANTHROPIC_API_KEY", "")
    if key and key != "CHANGE_ME":
        try:
            async with httpx.AsyncClient(timeout=180) as c:
                r = await c.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                             "content-type": "application/json"},
                    json={"model": model, "max_tokens": max_tokens,
                          "messages": [{"role": "user", "content": prompt}]})
            if r.status_code == 200:
                text = _text_of(r.json())
                if text is not None:
                    return text
                log.warning("llm.no_text_block", model=model,
                            blocks=[b.get("type") for b in (r.json().get("content") or [])])
            else:
                log.warning("forge.llm_failed", status=r.status_code,
                            detail=r.text[:200])
        except Exception as e:                                   # noqa: BLE001
            log.warning("forge.llm_error", error=str(e)[:200])

    return await _via_openrouter(prompt, model, max_tokens)


async def _claims_for(need_id: int, limit: int = 14) -> list[dict]:
    """Cited claims, each with the evidence that backs it."""
    rows = await db.fetch(
        """
        SELECT c.id, c.text, c.kind, e.url, e.title, left(e.sha256, 12) AS sha,
               left(e.body, 700) AS snippet
          FROM claims c JOIN evidence e ON e.id = c.evidence_id
         WHERE c.need_id = $1 AND e.substantive AND e.live_at_capture
           -- 🔴 NEVER hand generation a claim the verifier has REJECTED.
           -- `supported IS FALSE` means the fact-checker read the claim against
           -- its own source and said no. Offering it to the next generation
           -- invites the artifact to cite a known-false statement — and it did:
           -- claims 30 and 34 were cited 19 times across three tiers AFTER
           -- being marked unsupported, which is why `forge repair` could not
           -- clear them. NULL is allowed: unverified is not disproven, and on a
           -- first run every claim is NULL.
           AND (c.supported IS NULL OR c.supported IS TRUE)
         ORDER BY c.kind, c.id LIMIT $2
        """, need_id, limit)
    return [dict(r) for r in rows]


def _claims_block(claims: list[dict]) -> str:
    return "\n\n".join(
        f"[claim {c['id']} · {c['kind']}] {c['text']}\n"
        f"  SOURCE: {c['url']}  (sha256 {c['sha']})\n"
        f"  EXCERPT: {(c['snippet'] or '')[:400]}"
        for c in claims)


async def generate_section(need: dict, section: Section, claims: list[dict],
                           tier: str) -> Optional[GeneratedSection]:
    prompt = (
        f"You are writing ONE SECTION of a professional {tier} document that a "
        f"business will PAY FOR. Write only this section.\n\n"
        f"PRODUCT TITLE: {need['title']}\n"
        f"PROBLEM: {need.get('pain_statement') or need['title']}\n"
        f"AUDIENCE: {need.get('audience') or 'small and mid-sized businesses'}\n\n"
        f"SECTION: {section.heading}\n"
        f"WHAT THIS SECTION MUST DO: {section.brief}\n"
        f"MINIMUM LENGTH: {section.min_words} words.\n\n"
        f"RESEARCH YOU MAY CITE — these are real, fetched, hashed sources:\n"
        f"{_claims_block(claims)}\n\n"
        "RULES:\n"
        "- Write in Markdown. Start with '## " + section.heading + "'.\n"
        "- Every FACTUAL claim about the market, competitors or pricing must "
        "cite one of the claim ids above, inline, like [claim 42].\n"
        "- Do NOT invent statistics, vendor names or prices that are not in the "
        "research above. If you do not have evidence for something, say what "
        "would need to be checked instead.\n"
        "- No placeholders, no 'TBD', no 'coming soon'.\n"
        "- Write for an operator who will act on this, not for a brochure.")

    text = await _llm(prompt, max_tokens=2400)
    if not text:
        return None
    words = len(text.split())
    # `cla[a-z]{2}` not `claim`: the model misspells the marker (observed
    # `[claip 33]`, `[claik 28]`), and a citation lost to a typo is a claim
    # silently dropped from the Sources block. Matches forge/verify.py.
    used = [int(m) for m in re.findall(r"\[cla[a-z]{2} (\d+)\]", text, re.I)]
    return GeneratedSection(key=section.key, heading=section.heading, text=text,
                            words=words, claim_ids=sorted(set(used)))


async def build_tier(need_id: int, tier: str, *, run_id: Optional[int] = None,
                     max_sections: Optional[int] = None) -> dict:
    """Generate every section of one tier."""
    need = await db.fetchrow(
        "SELECT id, title, pain_statement, audience FROM needs WHERE id = $1", need_id)
    if need is None:
        raise LookupError(f"need {need_id} does not exist")

    p = await ev.params()
    cap = max_sections or int(p.get("forge_max_sections", 8))
    plan = sections_for(tier, max_sections=cap)      # the cap truncates the PLAN
    claims = await _claims_for(need_id)

    if not claims:
        return {"tier": tier, "sections": [], "error":
                "no cited claims available — Phase B must run first; a tier "
                "generated without evidence is exactly what C4 forbids"}

    out: list[GeneratedSection] = []
    for s in plan:
        gen = await generate_section(dict(need), s, claims, tier)
        if gen is None:
            log.warning("forge.section_failed", need_id=need_id, tier=tier,
                        section=s.key)
            continue
        out.append(gen)
        log.info("forge.section", need_id=need_id, tier=tier, section=s.key,
                 words=gen.words, claims=len(gen.claim_ids))

    if out:
        save_draft(need_id, tier, out)
    return {"tier": tier, "planned": len(plan), "sections": out,
            "claims_available": len(claims)}


_LEADING_HEADING = re.compile(r"^\s*#{1,6}\s+(.*\S)\s*$")


def _norm_heading(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def section_body(s: GeneratedSection) -> str:
    """Emit `## <planned heading>` + body. THE PLAN OWNS THE HEADING.

    🔴 Observed 2026-08-08 on `need-13-deployed`: the model emitted
    `# Who This Is For` — one `#`, not two — so `structural()` reported the
    section MISSING while every word of its content was present, and the whole
    artifact was withheld over a single character.

    The failure is worse than it looks. The model is asked to reproduce the
    heading verbatim, which makes the contract depend on formatting compliance
    on every section of every tier of every run, at $6 a run to find out. The
    heading is the one part of a section we already know before generating it,
    so it is not something to ask for and then re-buy when it comes back wrong.

    Any leading heading whose text matches the planned one is dropped and
    replaced with the canonical form. A first line that is a heading but says
    something ELSE is left alone — that is content, not a malformed title.
    """
    body = s.text.strip()
    lines = body.splitlines()
    if lines:
        m = _LEADING_HEADING.match(lines[0])
        if m:
            found, want = _norm_heading(m.group(1)), _norm_heading(s.heading)
            if found == want or found.startswith(want):
                body = "\n".join(lines[1:]).strip()

    # 🔴 DEMOTE THE MODEL'S OWN `#`/`##` HEADINGS TO `###`.
    #
    # The plan owns `##`; everything the model writes inside a section is
    # subordinate to it. A model-authored `##` sub-heading is not merely untidy,
    # it CORRUPTS VERIFICATION: `structural()` measures a section's length by
    # splitting the document on `\n## `, so the first sub-heading ends the
    # section as far as the word count is concerned.
    #
    # Measured 2026-08-09 on need 13. The `estimate` section contained 598 words
    # and opened with `## What the pricing evidence actually supports`, so the
    # measured chunk was the heading alone — "Effort, Cost & Confidence", four
    # words — and the roadmap tier was withheld from sale as a THIN SECTION for
    # a document that was never thin. Regenerating it produced 647 words and
    # changed nothing, because the defect was in the measurement.
    body = re.sub(r"(?m)^(#{1,2})(?!#)\s+", "### ", body)

    return f"## {s.heading}\n\n{body}".rstrip()


def render(need: dict, tier: str, sections: list[GeneratedSection],
           claims: list[dict]) -> str:
    """Assemble the document, ending with a real citation list.

    The citation block is generated from the claims the sections actually
    referenced — so it cannot drift from the text, and a reader can check any
    statement against a hash.
    """
    used_ids = sorted({cid for s in sections for cid in s.claim_ids})
    by_id = {c["id"]: c for c in claims}

    parts = [
        f"# {need['title']} — {tier.title()}",
        "",
        f"> {need.get('pain_statement') or ''}".strip(),
        "",
        f"*Audience: {need.get('audience') or 'small and mid-sized businesses'}*",
        "",
        "---",
        "",
    ]
    for s in sections:
        parts += [section_body(s), ""]

    parts += ["---", "", "## Sources", "",
              "Every factual claim above is numbered and traceable to a page "
              "that was fetched and hashed at research time.", ""]
    if used_ids:
        for cid in used_ids:
            c = by_id.get(cid)
            if not c:
                continue
            parts.append(f"- **[claim {cid}]** {c['text'][:200]}  \n"
                         f"  {c['url']}  \n"
                         f"  `sha256 {c['sha']}`")
    else:
        parts.append("_No external factual claims were made in this document._")
    parts += ["", "---", "",
              f"<sub>Generated by JarvisProductDevelopment. "
              f"{len(sections)} sections, "
              f"{sum(s.words for s in sections):,} words, "
              f"{len(used_ids)} cited sources.</sub>"]
    return "\n".join(parts)


async def package(need_id: int, tier: str, sections: list[GeneratedSection],
                  *, run_id: Optional[int] = None) -> dict:
    """Write the artifact to disk, content-addressed, and record it.

    🔴 The file is written BEFORE the row. `delivery.mint()` refuses to issue a
    download token unless the file exists on disk — all three of Pimlico's
    delivery tokens point at files that do not exist. Writing the row first
    would make that possible again.
    """
    need = await db.fetchrow(
        "SELECT id, title, pain_statement, audience FROM needs WHERE id=$1", need_id)
    claims = await _claims_for(need_id)
    body = render(dict(need), tier, sections, claims)
    raw = body.encode()
    sha = hashlib.sha256(raw).hexdigest()

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACT_DIR / f"need-{need_id}-{tier}-{sha[:12]}.md"
    path.write_bytes(raw)

    used_ids = sorted({cid for s in sections for cid in s.claim_ids})

    aid = await db.fetchval(
        """
        INSERT INTO artifacts (need_id, solution_id, tier, kind, sha256, bytes,
                               storage_uri, title, sections, words, run_id)
        VALUES ($1, NULL, $2, 'markdown', $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (need_id, tier) WHERE need_id IS NOT NULL DO UPDATE
          SET sha256 = EXCLUDED.sha256, bytes = EXCLUDED.bytes,
              storage_uri = EXCLUDED.storage_uri, sections = EXCLUDED.sections,
              words = EXCLUDED.words, run_id = EXCLUDED.run_id,
              structural_ok = NULL, factual_ok = NULL, offerable = FALSE
        RETURNING id
        """,
        need_id, tier, sha, len(raw), f"file://{path}", need["title"][:280],
        len(sections), sum(s.words for s in sections), run_id)

    # Attach the cited claims to this deliverable via the JOIN TABLE.
    # `claims.deliverable_id` is single-valued, so using it made each tier steal
    # the citations from the previous one — two artifacts were then marked
    # verified because they had no claims left to check.
    await db.execute("DELETE FROM artifact_claims WHERE artifact_id = $1", int(aid))
    if used_ids:
        await db.execute(
            "INSERT INTO artifact_claims (artifact_id, claim_id) "
            "SELECT $1, unnest($2::bigint[]) ON CONFLICT DO NOTHING",
            int(aid), used_ids)

    log.info("forge.packaged", need_id=need_id, tier=tier, artifact_id=int(aid),
             bytes=len(raw), words=sum(s.words for s in sections),
             cited=len(used_ids), path=str(path))
    return {"artifact_id": int(aid), "tier": tier, "sha256": sha,
            "bytes": len(raw), "path": str(path),
            "sections": len(sections), "words": sum(s.words for s in sections),
            "cited_claims": len(used_ids)}


async def repair_section(need_id: int, tier: str, section_key: str,
                         extra_brief: str = "") -> dict:
    """Regenerate ONE section of one tier, in place, and re-save the draft.

    🔴 WHY THIS EXISTS. Fixing a single defective section used to mean
    `jpd forge run`, which regenerates all three tiers at $6-9 and overwrites
    every draft. Measured on need 13: the `estimate` section came back at FOUR
    WORDS against a 120-word minimum, and that one flaw withheld the roadmap
    tier from sale. Paying six dollars and replacing two good artifacts to fix
    four words is not a repair, it is a rebuild.

    One section, one LLM call. Everything else on disk is left exactly as it
    was, so what was verified stays verified.
    """
    plan_sections = sections_for(tier)
    section = next((s for s in plan_sections if s.key == section_key), None)
    if section is None:
        raise ValueError(
            f"unknown section {section_key!r} for tier {tier!r}; "
            f"expected one of {[s.key for s in plan_sections]}")

    draft = load_draft(need_id, tier)
    if not draft:
        raise ValueError(
            f"no draft on disk for need {need_id} tier {tier!r} — "
            f"run `jpd forge run {need_id}` once to generate it")

    need = await db.fetchrow(
        "SELECT id, title, pain_statement, audience FROM needs WHERE id=$1", need_id)
    if need is None:
        raise LookupError(f"no need {need_id}")
    claims = await _claims_for(need_id)
    if not claims:
        raise ValueError("no cited claims — phase B must run first")

    if extra_brief:
        section = Section(key=section.key, heading=section.heading,
                          brief=f"{section.brief}\n\n{extra_brief}",
                          min_words=section.min_words)

    before = next((s for s in draft if s.key == section_key), None)
    gen = await generate_section(dict(need), section, claims, tier)
    if gen is None:
        raise RuntimeError(
            f"regeneration of {tier}/{section_key} returned nothing — the draft "
            f"is UNCHANGED on disk, so nothing was lost")

    # Replace in place, preserving the plan's section ORDER. Appending would
    # move the section to the end of the document and `structural()` checks
    # headings, not order — so the damage would be invisible to verification.
    replaced = False
    for i, s in enumerate(draft):
        if s.key == section_key:
            draft[i] = gen
            replaced = True
            break
    if not replaced:
        order = [s.key for s in plan_sections]
        draft.append(gen)
        draft.sort(key=lambda s: order.index(s.key) if s.key in order else 99)

    save_draft(need_id, tier, draft)
    log.info("forge.section_repaired", need_id=need_id, tier=tier,
             section=section_key, words_before=(before.words if before else 0),
             words_after=gen.words, min_words=section.min_words)
    return {"need_id": need_id, "tier": tier, "section": section_key,
            "words_before": before.words if before else 0,
            "words_after": gen.words, "min_words": section.min_words,
            "meets_minimum": gen.words >= section.min_words,
            "claim_ids": gen.claim_ids}
