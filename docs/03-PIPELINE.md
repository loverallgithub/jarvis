# 03 — JarvisProductDevelopment: The Pipeline, Step by Step

> Version 0.1, 2026-08-07. Every step below is a `@step`-decorated unit per `02-ARCHITECTURE.md §2`:
> it declares inputs, outputs, required connectors, an **acceptance predicate**, a cost budget,
> and a test path. A step with no acceptance predicate cannot be registered.
>
> **Legend** — 🤖 fully automated · 🧑 human task (blocking, typed) · 🔀 gate (operator decision)
> · 💰 produces a sellable artifact

---

## Phase A — DISCOVER THE NEED

*Goal: a **Need** that is frequent, painful, cross-corroborated, and held by people who can pay.*

### A1 🤖 `discovery.harvest`
Pull from every `live` source. Sources are registry rows, not code.

- `search` — Google Suggest (product-intent autocomplete), Google Trends
- `launch` — Product Hunt, Indie Hackers
- `community` — Hacker News, Reddit (slow-drip, rate-respecting), Discourse forums, StackOverflow, GitHub Issues
- `review` — App Store 1–3★ on B2B tooling
- `filing` — SEC EDGAR 10-K Item 1A (SIC 6000–6799 excluded at source: FS conflict restriction)
- `authority` — **new**: creator channels (§A1b)

**Acceptance:** `≥ 1 live source returned ≥ 1 item` **and** every source's yield recorded.
**Zero-yield is a failure signal**, not a quiet success — it increments `zero_yield_streak` and
walks the connector toward dormant. *(Pimlico has three sources at 0/day with `dormant: []`.)*

### A1b 🤖 `discovery.harvest_authority`
For each channel in the authority registry — **Alex Hormozi, Leila Hormozi, Codie Sanchez,
Skool, Liam Ottley, Liam Evans, Jack Roberts**, plus any added later:

> **Build status 2026-08-08 — step 1 only** (`02-ARCHITECTURE.md §22`). Six connectors exist
> and are **dormant pending the key** (`runbooks/HT-002`); Skool has none. Steps 2–4 are not
> built, so the acceptance predicate below **cannot yet be satisfied** and no authority
> signal may reach `claims`. Dedup is by `(source_id, external_id)` rather than a watermark,
> matching the other eight sources.

1. YouTube Data API v3 → uploads playlist → new videos since watermark (**clamped**) ✅ built
2. Transcript via captions; fallback audio → whisper on the local ollama host ⛔ not built
3. LLM extraction against a strict schema: ⛔ not built — needs API budget
   ```json
   { "problem_statement": "...", "audience": "...", "evidence_quote": "...",
     "timestamp_s": 412, "stated_cost_or_pain": "...", "is_explicit_ask": true }
   ```
4. Write `evidence` (url = `watch?v=…&t=412`, sha256 of the transcript segment) **and** `signal`

**Acceptance:** every emitted signal has a resolvable timestamped URL and a non-empty
`evidence_quote`. A signal without a verbatim quote is discarded — this is what makes a
high-weight opinion source safe.

> **Skool** has no public API → `kind="browser"` with a `kind="human"` fallback
> (`runbooks/HT-003`). Paid-community threads are the highest-intent pain source available.

### A1c 🤖 `discovery.harvest_tubeonai`
Where TubeOnAI covers a tracked channel, consume its summary instead of transcribing — it is a
**transport optimisation for `authority`**, not a new evidence type.

Ships **dormant**. `contract_test()` must pass against a real credential before it emits anything
(`api.tubeonai.com` is confirmed a real API host from this box; its endpoint contract is
unverified — see `02-ARCHITECTURE.md §6`).

**Acceptance:** every emitted signal resolves back to a real video URL, and is flagged
`evidence.source_kind='paraphrase'` until a verbatim timestamped quote is captured from the
source video. A paraphrase can promote a need; it can **never** back a published claim — the
publish predicate rejects it.

### A1d 🤖 `discovery.capture_voices` *(operator request, 2026-08-07)*
**For every signal, capture who said it.** The author is already in every payload we parse; Pimlico
parsed it and threw it away.

Per signal, extract and upsert a `voice`:

| Source type | What is captured |
|---|---|
| `community` | Reddit/HN/Discourse/StackOverflow author handle + profile URL; GitHub issue author + their org |
| `review` | App Store reviewer display name *(pseudonymous — evidence only)* |
| `filing` | The **filing company** — CIK, ticker, name. Always a `company` voice |
| `authority` | The creator + **any person or company they name** as having the problem |
| `launch` | Product Hunt / Indie Hackers maker + their company |
| **comments** | Commenters on the above who corroborate the pain — captured with `stance` |

Each becomes a `voice_mentions` row carrying `stance` (`reports_pain`, `requests_solution`,
`offers_workaround`, `sells_alternative`, `endorses`), the verbatim quote, and the `evidence_id`.

**Acceptance:** ≥ 80% of signals resolve to an identified voice, **and** `do_not_contact` is `true`
for every voice sourced from a community platform.

> ⚠️ **These are evidence, not a mailing list.** `do_not_contact` defaults **true**. Promotion to
> contactable requires an explicit recorded lawful basis (a public professional profile, or an
> opt-in). Enrichment applies to **`company` voices only** — never to private individuals. Any
> outreach to a voice is approval-gated (F6). Getting this wrong is a legal and reputational
> problem, not a growth tactic.

### A2 🤖 `discovery.normalise`
Deduplicate, embed (`nomic-embed-text` on local ollama, vectors in local qdrant — **zero API
cost**), admit to the 30-day rolling window.

**Admission rule:** a concept needs **≥ 4 content words**. Bare brand names and one-word review
titles embed as mutually similar and previously formed a 20-member false cluster in Pimlico that
would have cleared every gate and auto-built garbage.

### A3 🤖 `discovery.cluster`
Semantic clustering on cosine distance. Lexical fallback if a side-car is down — **discovery
never fails because ollama or qdrant is unavailable.** Runs off the event loop
(`asyncio.to_thread`); measured at 181s for 1,690 signals in Pimlico, which outlived the lease
and froze HTTP when run inline.

### A4 🤖 `discovery.gate` 🔀
Five gates, **all** must pass. Thresholds are **rows in the database**, not constants.

| Gate | Default | Rationale |
|---|---|---|
| frequency | ≥ 5 independent mentions | One person's complaint is not a market |
| severity | ≥ 4.0 / 5 | Averaged over **pain evidence only** — launches don't count |
| cross-source | ≥ 2 distinct `source_type` | ⚠️ **`authority` cannot self-corroborate** |
| recency | ≥ 1 mention in last 7d | Kills dead trends |
| commercial intent | ≥ 1 signal of spend/tooling/hiring | Pain without budget is not a product |

Every evaluation — pass **and** fail — is written to `gate_evaluations` with value and threshold.
This is what makes calibration possible; Pimlico's census lived in process memory and was lost on
every restart, which is why three weeks of accumulation produced zero promotions with no way to
diagnose it.

**Counterfactual replay** is then a SQL query: *"what would have promoted at severity ≥ 3.5?"*

### A5 🤖 `discovery.qualify`
For survivors: who holds this pain, and can they pay? **This step now reads the `voices` attached
to the cluster** rather than inferring an audience — qualification becomes a list of named
organisations, not a guess. Enrichment via **Databar** (160+ providers) on `company` voices only;
where a `filing` signal is present, recover tickers/CIKs and pull real revenue.

**Acceptance:** ≥ 3 named `company` voices with a revenue band, or a quantified audience.
A need that cannot be qualified is **parked**, not built.

> **Distinct-voice check.** Severity and frequency are recomputed over **distinct voices**, not raw
> mentions. Five mentions from five people is a market; five mentions from one person is one loud
> person. Pimlico's frequency gate could not tell those apart — this is the cheapest real
> improvement to promotion quality available, and it exists only because A1d captured the author.

### A6 🤖 `discovery.score` 💰→ **Need Dossier**
Weighted sub-scores → total /10. `gap` is **no longer scored without competitive data** — it is
deferred to Phase B and the need carries `gap: null` until then. *(Pimlico weighted `gap` at 0.25
— second-highest — with no competitive data at all.)*

### A7 🔀 `#discoveries` — **Promotion decision**
Card posted with the **gate census that let it through**, the qualification, and the top three
evidence quotes with clickable timestamps. Auto-promote above threshold; otherwise operator
decides.

---

## Phase B — RESEARCH THE SOLUTION

*Goal: a **Research Dossier** where every finding is a hashed, live-at-capture, citable source.*

### B1 🤖 `research.capture_competitors`
Who already solves this? **you.com Research** (`lite`, $0.012/call) + targeted fetches.
Every result stored as `evidence` — url, sha256, fetched_at, http_status, snippet, full artifact.

**Acceptance:** ≥ 5 evidence rows, each with a hash and a live-at-capture flag.

### B2 🤖 `research.gap_analysis`
What is missing, badly done, or over-priced. **Every gap statement is a `claim` with a
`NOT NULL evidence_id`.** An unsourced gap cannot be written.

Back-fills `needs.gap` — the score that Phase A deferred.

### B3 🤖 `research.willingness_to_pay`
Observed prices for adjacent solutions, captured as evidence with URLs.
**Never a regex over one page.** *(Pimlico priced €297 products from exactly that.)*
→ `dossiers.kind='pricing'`

### B4 🤖 `research.feasibility`
Can we build it with what we own? Resolves against the **connector registry and the products
inventory** — real capability, not a wish list. Marks per-tier feasibility:

- Roadmap: always feasible
- Instructions: feasible if every step can be *described* precisely
- **Deployed: feasible only if every required connector is `live` and has a passing contract test**

This is what stops the Deployed tier over-promising. If it is infeasible, **the other two tiers
still sell** — the ladder degrades gracefully.

### B5 🤖 `research.synthesise` 💰→ **Research Dossier**
**Acceptance:** ≥ 15 evidence rows · **0 uncited factual claims** · every URL still resolving at
capture time · feasibility decided per tier.

---

## Phase C — CREATE THE SOLUTION ROADMAP  💰 **TIER 1 — SELLABLE**

*Goal: "Here is exactly what to build, in what order, with what tools, at what cost, and why."*

### C1 🤖 `roadmap.define_outcome`
The measurable result and how the buyer knows they got it. One sentence, one metric, one
timeframe — all three cited to the Research Dossier.

### C2 🤖 `roadmap.phase_plan`
Milestones with dependencies, durations, owners, and an explicit critical path.
**Acceptance:** every milestone has an owner, a duration, and ≥ 1 dependency edge or an explicit
"no dependencies" marker. No orphan milestones.

### C3 🤖 `roadmap.select_stack`
Chosen from the **owned products registry first**, then free/open tooling, then paid third
parties — each with the reason and the cost. Anything with no API is flagged as requiring manual
operation, honestly, up front.

### C4 🤖 `roadmap.estimate`
Effort, cost, and a confidence interval. Anchored on real Phase B pricing evidence.

### C5 🤖 `roadmap.risk_register`
What kills this, how likely, what to do instead. Each risk cited.

### C6 🤖 `roadmap.package` 💰→ **THE ROADMAP**
**Acceptance:** outcome measurable · every milestone owned and sequenced · every tool named with
a cost · ≥ 1 risk with a mitigation · **0 uncited factual claims** · renders to PDF + HTML.

---

## Phase D — OUTLINE & SPECIFY  💰 **TIER 2 — SELLABLE**

*Goal: a competent operator can execute the roadmap without asking a single question.*

### D1 🤖 `outline.decompose`
Roadmap milestones → modules → concrete build steps.

### D2 🤖 `outline.write_instructions`
For each step: **what, why, how, where** — exact clicks, exact field names, exact commands, exact
screens. Same discipline as a `human_task`, because it *is* one: instructions for a human.

### D3 🤖 `outline.configuration`
Every credential, env var, DNS record, webhook, and permission the buyer needs — with where to
get it. Traps documented (this is where hard-won knowledge earns its price).

### D4 🤖 `outline.acceptance_tests`
Tests **the buyer** can run to prove their build works. Copy-pasteable, with expected output.

### D5 🤖 `outline.package` 💰→ **THE INSTRUCTIONS** *(= Roadmap + build manual)*
**Acceptance:** every roadmap milestone has ≥ 1 instruction step · every step has a verification ·
every credential documented with an acquisition path · **0 uncited factual claims** ·
**a dry-run reviewer step confirms no instruction references an undefined prior state.**

---

## Phase E — BUILD THE SOLUTION  💰 **TIER 3 — SELLABLE**

*Goal: the working thing, built, configured, tested, handed over.*

### E1 🤖 `forge.scaffold`
Materialise the structure from D1's decomposition.

### E2 🤖 `forge.generate`
**One LLM call per section/module**, never one per product — this is what produced Pimlico's
genuine 24–28k-word depth and it is the right call. Diagrams drawn programmatically, not fetched.

Cost is budgeted per step. A size cap **also truncates the plan** so `verify` cannot then trip on
"fewer sections than planned" — Pimlico's cap guaranteed terminal failure by preserving paid-for
work and then failing the completeness check on it.

### E3 🤖 `forge.assemble`
Wire the modules. Where the solution is an automation, emit real workflow definitions. Where it
is a document set, emit the documents. Where it is a deployment, emit the stack.

### E4 🤖 `forge.verify` — **structural AND factual**
Two distinct checks, because Pimlico only ever had the first:

- **Structural** — sections present, word counts, no `lorem ipsum`/`TBD`, all links resolve
- **Factual** — **every claim checked against its cited evidence snippet.** A claim whose
  evidence does not support it is marked and **repaired**, not retried.

**Acceptance:** structural pass · **0 unsupported claims** · **0 uncited claims** · every D4
acceptance test executed and green.

> Repair branches on the **durable `repair_count`** with a hard ceiling. Pimlico's guard tested
> `attempts`, which was reset to 0 on every stage transition, so the loop could never terminate.

### E5 🤖 `forge.package` 💰→ **THE DEPLOYED PRODUCT**
Content-addressed artifacts, one per tier, hashes recorded.

### E6 🔀 **Build gate** — operator approves before anything becomes purchasable.

---

## Phase F — MARKET

### F1 🤖 `market.position`
Positioning from the **pain language captured in Phase B** — the buyer's own words, cited. Not
invented adjectives.

### F2 🤖 `market.copy`
Headline, subhead, benefits, objections, FAQ — **per tier**, because three buyers are not one
buyer. Every factual claim cited.

### F3 🧑 `market.copy_variants` — **SINTRA INSTRUCTION CARD** → `#sintra`
Sintra is Cloudflare-blocked from this VPS (verified failing daily since ≈07-25). It is therefore
a `kind="human"` connector, **not an API**. JPD posts a fully-formed instruction card to the
`#sintra` Telegram topic containing the exact prompt, built from real dossier evidence; you paste
it into Sintra and reply with the output.

- The reply is **parsed against a schema** — a failed parse re-asks rather than persisting
- `SKIP <reason>` is an explicit, recorded operator decision
- **Nothing Sintra-shaped can ever auto-publish again**
  *(Pimlico published `"[Automation failed: Page.goto: Timeout 30000ms exceeded...]"` to a real
  LinkedIn account on 08-02, 03, 04, 05, 06 and 08-07.)*

Full card format: `02-ARCHITECTURE.md §7`.

### F4 🤖 `market.media`
Sizzle image (photographic, generated from the real promise/audience — ~$0.039 via
gemini-2.5-flash-image) · showcase video (SuperCool, **~544 credits per 8s clip — measured, its
docs claim 68 and are wrong by 8×**) · cover.

**Re-use one video across channels.** Do not generate per-channel variants without a budget
decision. `SHOWCASE_VIDEO_ENABLED=false` stops all future video spend.

### F5 🤖 `market.pages`
Self-hosted sales page per solution, **with a tier selector** — three offers, one page, one
checkout. Video hero + social player cards. GHL cannot create landing pages via API (verified);
self-hosting is correct and already proven.

### F5b 🔀 `market.launch_to_voices` — approval-gated *(operator request, 2026-08-07)*
**The highest-conversion audience for a solution is the people who described the problem.**

The `voices` attached to this need — filtered to `contactable = true` and
`do_not_contact = false`, i.e. those with a recorded lawful basis — become the launch list. The
outreach quotes **their own words back**, with the citation, and offers the tier that matches
their stance:

| Stance | Offer |
|---|---|
| `requests_solution` | **Deployed** — they asked for the thing |
| `reports_pain` | **Instructions** — they have the problem and some capability |
| `offers_workaround` | **Roadmap** — they are already building; sell them the plan |
| `sells_alternative` | **Nothing.** They are a competitor. Excluded automatically |

**Acceptance:** every recipient has a recorded lawful basis, an unsubscribe path, and a citation
linking back to their own quote. The step **refuses to run** if any recipient fails those checks —
it does not silently drop them, it fails and reports which.

> Community-sourced authors are excluded by default and stay excluded until a lawful basis is
> recorded per voice. This step is opt-in per launch, never a standing automation.

### F6 🔀 `market.distribute` — approval-gated
Cold email (Success.ai, throttled — the limit is enforced **in the workflow**, not merely
advised) · prospect sourcing (Getlead → GHL upsert, so relaunches **merge** tags rather than
silently rejecting duplicates) · social (Content360, human-approved copy only) ·
webinar registration (WebinarKit — registration is automatable, **creation is UI-only**).

> Outward-facing, irreversible actions default to a human decision. Success.ai currently fires
> unconditionally in Pimlico by explicit prior instruction; JPD re-gates it by default, and
> ungating is a deliberate config change.

---

## Phase G — DELIVER

### G1 🤖 `commerce.receive_payment`
Webhook → signature verified → `orders` row with `signature_valid`.
**A failed signature cannot fulfil.** Amount is compared to the `offers` row — never trusted from
the payload. *(Pimlico treated any `amount > 0` as a paid order and would mint a €297 product for
`amount: 1`.)*

### G2 🤖 `commerce.grant_entitlement`
`entitlements(order_id, buyer_ref, solution_id, tier)`. Tier-scoped. Idempotent on `provider_ref`.

### G3 🤖 `commerce.fulfil`
Deliver **exactly the purchased tier's** artifact. Token minted **only after** the artifact file
is confirmed to exist on disk — a consistency check at mint time plus a periodic sweep.
*(All three of Pimlico's existing delivery tokens point at files that do not exist.)*

### G4 🤖 `commerce.notify`
Buyer email via GHL conversations (proven) with Mailgun as fallback (also proven). Every
notification failure is a **metric**, not a log line.

### G5 🤖 `commerce.upgrade_path`
Post-purchase: offer the next tier at `price_delta_minor`. On purchase, fulfil **only the delta**.
Highest-margin revenue in the system, zero extra production cost.

### G6 🤖 `commerce.attribute`
Which source type, which need, which channel produced this order. Closes the loop back to Phase A
and is what eventually makes gate calibration data-driven rather than guessed.

---

## The loop, closed

```
  A. DISCOVER ──► B. RESEARCH ──► C. ROADMAP 💰 ──► D. INSTRUCTIONS 💰 ──► E. DEPLOYED 💰
       ▲                                │                  │                    │
       │                                └──────────────────┴────────────────────┘
       │                                                   │
       │                                            F. MARKET ──► G. DELIVER
       │                                                                │
       └──────────────── G6 attribution ◄───────────────────────────────┘
                    (which source type actually produced revenue
                     → re-weight sources → re-tune gates)
```

**This is the loop Pimlico never closed.** Its discovery has never promoted autonomously and its
delivery has never processed a real order, so nothing has ever flowed back from revenue to
sourcing. Phase 8 of the build order (`02-ARCHITECTURE.md §11`) exists specifically to close it,
and until it is closed, gate thresholds are informed guesses.
