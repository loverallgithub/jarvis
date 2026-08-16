# JPD CHECKPOINT — generated

> Generated from the `checkpoints` table by `jpd checkpoint render`.
> **Resume rule:** re-run the last step's acceptance predicate before assuming
> anything. A missing DONE line is not evidence of missing work.

**Latest checkpoint:** `phase-f-run-live-four-false-positives-v61` — v61, 542 tests green. Phase F RUN LIVE end to end for the first time, plus jpd forge repair and jpd market recopy, plus FOUR FALSE POSITIVES FOUND IN OUR OWN CHECKERS. 🟢 ALL THREE ARTIFACTS NOW PASS STRUCTURAL for the first time (structural_ok=t on 6,7,8). Only the factual gate remains: 3 unsupported claims of 14. TWO NEW TOOLS. jpd forge repair <need> <tier> <section> regenerates ONE section for one LLM call instead of jpd forge run at -9 which rebuilds all three tiers and overwrites every draft; it re-packages and re-checks STRUCTURE ONLY (free) because the factual pass is 14 calls and belongs to reverify, so a repair never silently bills for verification nobody asked for. jpd market recopy <need> [--tier] [--block] [--below-floor] regenerates only failing copy blocks: 7 of 15 already cleared the floor, so re-running market.copy pays twice for work already right and replaces it with text that might not be. Tests pin that repair replaces a section IN PLACE -- appending would move it to the end and structural() checks heading PRESENCE not ORDER, so a reordered document passes verification while reading as nonsense -- and that a failed regeneration leaves the draft byte-identical. 🔴 FOUR FALSE POSITIVES, ALL THE SAME SHAPE: A CHECKER PUNISHING CORRECT WORK. (1) The placeholder rule flagged [your billing descriptor] and [your account email] -- buyer fill-in fields in email templates the buyer is meant to complete. It withheld artifacts 7 and 8 from sale for doing their job. Fixed by operator decision: the word "your" was removed from the alternation; insert/placeholder/xxx/TBD still fail. (2) 'uncited claims: 0' was a TAUTOLOGY -- claims.evidence_id is NOT NULL so the count can never be non-zero, making forge.verify's acceptance predicate unfailable and the second conjunct of factual_ok dead weight. Quoted as an achievement in every prior checkpoint. (3) The 'thin estimate section (4 words)' was a 598-WORD SECTION. structural() measures by splitting on \n## , and the model wrote its sub-headings at ## -- the level the plan owns -- so the measured chunk was the heading text alone, literally four words. The roadmap tier was withheld over a document that was never thin. Fixed by demoting model-authored #/## to ###. jpd forge repair is what made this cheap to find: one call proved regeneration changed nothing, so the defect had to be in the measurement. (4) service_promises() flagged 'there is no account team' and 'the document does not contact the vendor for you' -- DENIALS of service, which is exactly what the fixed prompt was asked to produce. Fixed with negation handling; the two failing tests pulled opposite ways ('We will NOT escalate' needs the negation found INSIDE the match; 'no setup fee, AND we restore' needs it ignored because a coordinator starts a new positive clause). LESSON WORTH KEEPING: a checker that punishes the right answer trains people to disable it, which is strictly worse than not having it. Four in one session, each withholding or penalising correct work. When a gate fires, check whether the thing it flagged is actually wrong BEFORE fixing the thing. PHASE F RESULTS. market.position now derives audience from the PRODUCT's own 'Who This Is For' section, not needs.audience which held the literal string '5 distinct voices' -- a COUNT in a field meant for a segment. Before the fix F1 invented 'Finance teams at 50-500 employees'; after it produced 'Owner-operator (1-15 employees, no finance hire)', matching the artifacts. Fifteen copy blocks would have been written for the wrong buyer at a price ladder anchored for a different one. market.copy FAILED ITS OWN 90% GATE, correctly: 8 of 15 blocks below floor. Root cause was MY framing -- TIER_BUYER described the deployed tier as 'a built, configured, tested, handed-over system' (the ladder's generic wording), so the model wrote service promises: 'We restore your login', 'We can get you access restored within 48 hours'. Nothing could cite those; the evidence is App Store reviews of someone else's app. Fixed by stating the deliverable is a DOCUMENT the buyer follows themselves, and by adding the mechanical service_promises() check -- separate from coverage because 'We'll handle it' has ZERO checkable assertions, sails through a coverage gate, and is the most dangerous sentence that could appear on a page. OPEN QUESTION FOR THE OPERATOR: six blocks remain below floor and the uncited sentences are now PRODUCT SELF-DESCRIPTIONS -- 'the document is approximately 40-50 pages', 'typically 20-40 minutes'. Those are checkable in principle but citable only against the artifact, never against the research. Counting them as uncited claims may be wrong. Deciding that is a judgement about what the metric means, and I stopped rather than make a fifth checker change the same day one of them flagged its own fix. Stack: core and console v61, commerce pinned v4, ghl_payments live, JPD store PhxzRjZIIfHmC06vqj8b live, 8905 closed, 542 tests.  
Phase `BUILD` · written 2026-08-09 08:39 UTC · resumable from `decide-whether-product-self-description-counts-as-uncited`

## Resumable runs

None. Every run has reached a terminal state.

## Open human tasks — 0

None open.

## Connectors not live — 21

| connector | state | fail streak | zero-yield streak |
|---|---|---|---|
| anthropic | dormant | 0 | 0 |
| app_store_reviews | dormant | 0 | 0 |
| databar | dormant | 0 | 0 |
| discourse | dormant | 0 | 0 |
| ghl | dormant | 0 | 0 |
| google_trends | dormant | 0 | 0 |
| indie_hackers | dormant | 0 | 0 |
| mailgun | dormant | 0 | 0 |
| reddit | dormant | 202 | 0 |
| sintra | dormant | 0 | 0 |
| skool | dormant | 0 | 0 |
| stripe | dormant | 0 | 0 |
| tubeonai | dormant | 0 | 0 |
| you_com | dormant | 0 | 0 |
| youtube_data_v3 | dormant | 109 | 0 |
| yt_alex_hormozi | dormant | 109 | 0 |
| yt_codie_sanchez | dormant | 108 | 0 |
| yt_jack_roberts | dormant | 108 | 0 |
| yt_leila_hormozi | dormant | 108 | 0 |
| yt_liam_evans | dormant | 108 | 0 |
| yt_liam_ottley | dormant | 108 | 0 |

<!-- HAND-WRITTEN BELOW — SURVIVES REGENERATION -->

## JarvisProductDevelopment — the hand-written half

> ⚠️ **Everything ABOVE the marker is regenerated** by `jpd checkpoint render` from the
> `checkpoints` table. **Everything below it is hand-written and survives** regeneration —
> this is where reasoning lives, and reasoning is the one thing a table cannot hold.
> `render` refuses to write to a file that has no marker, precisely so this section cannot
> be destroyed by running the command.
>
> **Resume rule (earned from Pimlico `[T-1.12]`):** on resume, **re-run the last step's
> acceptance predicate before assuming anything.** A missing DONE line is not evidence of
> missing work — a session that died after doing the work leaves the same trace as one that
> died before.

---

## Current state — 2026-08-08

**Phase:** ✅ **0 (skeleton)** · ✅ **1 (commerce) BUILT** — exit criterion partially met, a
real purchase still needs **HT-005** · ✅ **2 (console) — MET** · ✅ **3 (connectors +
observability) — MET** · ✅ **4 (discovery) — MET** · ✅ **5 (research/grounding) — MET** ·
✅ **6 (forge) — MET, with the factual pass blocked on API budget.**

Stack `jarvis`: postgres · redis · prometheus · alertmanager · core `jarvis/core:v39` ·
console `jarvis/core:v39` · commerce `jarvis/core:v4` (**pinned separately**).
**16 pipeline steps registered. 272 tests green — 0 failed, 0 errors, run inside the deployed
`v39` image 2026-08-08** (integration 133 · unit 99 · journey 24 · stages 16).
**10 of 32 connectors live** — count
connectors from `jpd connectors`, not from memory; earlier entries below said "11 of 13"
against a smaller registry and the two numbers are not comparable.
**2 needs promoted autonomously; 1 Research Dossier with 25 substantive hash-verified
evidence rows across 33 domains and ZERO uncited claims.**
**Next: HT-005, then lift the Anthropic cap, then build phase 7 — market.**

✅ **DEPLOYED 2026-08-08 — `jarvis/core:v39`, currently running.** D-016/D-017 went live in
`v37`; `v38` was a **no-op rebuild** to exercise the fixed `deploy.sh` (D-018), which is why
those two tags share one image ID; **`v39` carries the gate fix (D-019)** and has a genuinely
different image ID — `57703fb09fe21` vs `65f565024f5ae`. **So the v39 roll is the first time
the D-018 console convergence check actually discriminated**, rather than passing vacuously as
lesson 54 records. All rolls verified by **image ID**, not by "converged". **`commerce` stayed
on `v4`**: `COMMERCE_VERSION` was unchanged, so the money path did not move and the journey
tests were correctly skipped rather than silently bypassed. Migrations were a no-op (15/15
already applied). `jpd doctor`: **all checks passed**.

Proven on the live stack, not in the tree:
- `jpd connectors orphans` — the six `yt_*` rows are **gone from `rows_without_code`**; only
  `discourse`, `google_trends`, `skool`, `tubeonai` remain. `youtube_data_v3` sits in
  `code_without_rows` beside `anthropic`/`ollama`/`qdrant`, which is by design.
- `youtube_data_v3` and all six channels report **`JPD_YOUTUBE_API_KEY absent (HT-002)`** —
  dormant with an actionable reason, which is the whole point.
- `indie_hackers` now reports *"no longer publishes a feed… 200 with HTML… byte-identical
  shell"* instead of *"not well-formed XML"*. The misleading error is gone from production.
- **32 connectors registered, 10 live** (was 26/10 — the six channels are new rows).
  ⚠️ `app_store_reviews` flapped dormant mid-session on a transient Apple outage and is live
  again; see the correction in the sources table.

🔴 **The Anthropic key hit its usage limit** — `400 "You have reached your specified API usage
limits. You will regain access on **2026-09-01**"`. Generation completed before this;
**factual verification did not**. Every fact-check recorded "verification did not return a
usable answer" and the verifier correctly marked those claims **NOT supported**, so **no
artifact is `offerable`**. That is the designed behaviour: an unverifiable claim is not a
verified claim. Re-run `jpd forge run 13` once the cap is lifted.

> 🔴 **Re-verified 2026-08-08 — this is NOT a wait-until-September problem.** `probe` passes
> (`GET /v1/models` → 200) and only `POST /v1/messages` is refused, so the key is valid. The
> message says *"your **specified** API usage limits"* — it is a **self-imposed spend cap in
> the Anthropic Console**, org-level and/or per-key. **Raise it and the forge runs today.** A
> previous reading of this line as an Anthropic-side suspension would have idled the build
> for three weeks for no reason.

### Done
| ID | What | Artifact |
|---|---|---|
| D-001 | Live analysis of Pimlico as design input; 7 failure classes → 8 constraints (C1–C8) | `docs/00-ANALYSIS.md` |
| D-002 | Charter — full description of intent, three-tier ladder, definition of done | `docs/01-CHARTER.md` |
| D-003 | Optimal architecture — 4 services, step engine, connector contract, data model | `docs/02-ARCHITECTURE.md` |
| D-004 | Full pipeline, phases A–G, every step with acceptance predicate | `docs/03-PIPELINE.md` |
| D-005 | Human runbook: Telegram forum + Sintra thread | `runbooks/HT-001-telegram-forum.md` |
| D-006 | TubeOnAI source + voices/voice_mentions model + steps A1c/A1d/F5b (DEC-003, DEC-004) | `docs/02-ARCHITECTURE.md`, `docs/03-PIPELINE.md` |
| D-007 | Products inventory, 59 rows, Sheets-ready | `docs/products-inventory.csv`, `runbooks/HT-004` |
| D-008 | Pimlico: 4 defects fixed + LinkedIn publishing stopped (hermes v19, browser-agent v3) | `/opt/ops/checkpoints/CHECKPOINT.md` §4.40 |
| D-009 | **Build phase 0** — schema (26 tables), step engine, lease guard, connector state machine, clamped watermarks, checkpoints, `jpd` CLI, swarm stack, firewall. **79 tests green.** | `docs/02-ARCHITECTURE.md` §15, `platform/` |
| D-010 | **Build phase 1 — commerce.** `jarvis-commerce` deployed with an independent version pin; signature/amount/idempotency checks, hashed delivery tokens, artifact-existence gate, sweep, tier ladder, upgrade delta, attribution. **142 tests green incl. 26 journey/adversarial.** | `docs/02-ARCHITECTURE.md` §16, `runbooks/HT-005` |
| D-020 | **`app_store_reviews` app ids retuned — yield doubled, 8 → 16 admissible signals.** Four ids whose US feeds are *consistently* empty (Xero, FreshBooks, Wave, Invoice2go) replaced with **SwipeSimple `840326645`**, **Roll by ADP `1474007759`**, **Joist `592163563`**, **Workiz `1469769810`**. Every id confirmed via the iTunes lookup API as **genre=Business** AND confirmed to return feed entries on **3–4 separate probes**, never one. A `sources.config` UPDATE — no redeploy. Selected on **content quality**, not review volume: TurboTax had 47 low-star but is consumer, and Ramp/Relay complaints are mostly login bugs. ⚠️ **NOT COVERED BY THE TEST SUITE** — it is a `sources.config` UPDATE, so there is no code to test and **no test asserts which app ids are configured**. Its only verification is the live harvest (16 admissible). A future edit to those ids would break nothing in the 272. | `sources.config`, this table |
| D-019 | 🔴 **THE THIRD VACUOUS PASS — FOUND AND FIXED. A disabled gate was a passed gate.** `Verdict.passed` was `all(r.passed for r in results)` and `add()` silently skipped any gate absent from `gate_thresholds WHERE enabled` — so `all([])` promoted anything. **Measured: one cluster failing 6/6 gates promoted cleanly once the rows were disabled.** Dangerous because retuning gates is a DATA operation by design, so the supported way to tune a gate was the way to delete it; and disabling `cross_source` alone silently stops enforcing *"authority cannot self-corroborate"*. Production was never exposed (6/6 always enabled). Fixed by `REQUIRED_GATES` + `GateConfigError` raised **before** the Verdict exists, plus the emptiness guard. **272 tests green (8 new, all negatives).** | `docs/02-ARCHITECTURE.md` §24 |
| D-018 | **`deploy.sh` now verifies `console` like every other service.** Added `converge console` (image ID, not replica count) and a `/ready` check on 8905 — console previously had neither, despite being where `jpd` runs. Header rule restated as *"verify EVERY service by IMAGE ID"*. Verified by extracting the **real** `converge()`/`wait_ready()` from the script and running both against the live stack, positive **and** negative, then by a real roll to **`v38`**. ⚠️ **The v38 roll passed VACUOUSLY** — identical source means Docker gave `v37` and `v38` the same image ID (`65f565024f5ae`), so `converge` could not have told "rolled" from "did not roll" on that run. It proved the script *executes*; discrimination is proved separately against `v36`. | `platform/docker/deploy.sh` |
| D-017 | **`indie_hackers` retired to dormant-with-a-reason.** Investigated rather than re-pointed: the RSS feed was **removed**, not moved — ten candidate paths all serve HTML and the SPA returns a byte-identical body for nonsense paths, so no URL fix exists. Now raises a `ConnectorError` naming the real cause instead of failing on *"not well-formed XML"*, which read like a transient markup change. **264 tests green (2 new).** `launch` survives — `product_hunt` still returns valid Atom on the same base class, which also rules out our parser. | `docs/02-ARCHITECTURE.md` §23 |
| D-016 | **HT-002 — the `authority` connector.** Six `yt_*` sources that `jpd connectors orphans` reported as *"can never emit"* now have an implementation each, plus a `youtube_data_v3` credential-health connector. Quota-shaped: **12 units/harvest, not 600**. Refuses to resolve a channel from a display name. **17 new tests.** ✅ **Deployed in `v37`** (an earlier version of this row said "not deployed" — stale). Still **dormant, and correctly so: no key on this host**, so it was written against the published contract rather than observed responses. Step 1 of §A1b only. | `docs/02-ARCHITECTURE.md` §22, `runbooks/HT-002` |
| D-015 | **Build phase 6 — the forge. EXIT CRITERION MET.** Phases C/D/E as 5 `@step` units; per-section generation, content-addressed artifacts on the shared volume, structural + factual verification, per-tier acceptance tests. **3 artifacts / 3 tiers / 0 uncited claims / 11,283 words / 36 acceptance tests.** ⚠️ Factual pass blocked on API budget until 2026-09-01. | `docs/02-ARCHITECTURE.md` §21 |
| D-014 | **Build phase 5 — research & grounding. EXIT CRITERION MET.** Phase B as 5 `@step` units; content-addressed evidence with substantive/live flags, LLM gap extraction cited per page, willingness-to-pay across ≥2 domains, per-tier feasibility from live connectors. **25 usable evidence rows / 33 domains / 0 uncited claims; `gap` backfilled from NULL.** Credentials (ollama, qdrant, anthropic, openrouter) sourced from the Pimlico stack. **236 tests green.** | `docs/02-ARCHITECTURE.md` §20 |
| D-013 | **Build phase 4 — discovery. EXIT CRITERION MET.** Phase A as 6 registered `@step` units (`jpd steps` is no longer empty); stemming + overlap-coefficient clustering off the event loop; 6 gates with a fully persisted census and counterfactual replay. **2 needs promoted autonomously, one spanning 3 source types.** **233 tests green.** | `docs/02-ARCHITECTURE.md` §19 |
| D-012 | **Build phase 3 — connectors + observability. EXIT CRITERION MET.** 8 real connectors (5 of 6 source types, no credentials needed), health scheduler on postgres advisory locks, Prometheus + Alertmanager, 11 alert rules with **10 synthetic tests that fire**. **227 tests green.** | `docs/02-ARCHITECTURE.md` §18 |
| D-011 | **Build phase 2 — console. EXIT CRITERION MET.** `jarvis-console` deployed; human-task lifecycle, parsed reply schemas, decision cards, Sintra bridge, Telegram forum client, reply poller, notification channels. **C7 proven live: core scaled to 0/0 and a task was created AND answered.** **202 tests green.** | `docs/02-ARCHITECTURE.md` §17 |

### ✅ DECISIONS TAKEN BY THE OPERATOR — 2026-08-07
| ID | Decision | Consequence |
|---|---|---|
| **DEC-001** | **Tier ratios: 1× / 3–4× / 10–15×** (recommendation accepted) | Roadmap `1×`, Instructions `3–4×`, Deployed `10–15×`. The base `1×` is set per-solution from Phase B willingness-to-pay evidence, so the *ratio* is fixed and the *anchor* is researched. |
| **DEC-002** | **Same payment provider. Same Stripe account. Same GHL tenant. NEW GHL store for JPD.** | No new provider integration and no new merchant onboarding — Stripe is already connected and proven. A **separate store** gives JPD its own product namespace inside the shared tenant. ⚠️ That tenant is co-tenanted with an unrelated hiking/tours business (53 products, only ~10 Pimlico's) — **every product query must filter by store**, and the new store makes that filter reliable instead of name-guessing. New human task **HT-005** to create the store. |
| **DEC-003** | **TubeOnAI added as a discovery source** | Transport accelerator for `authority` (step A1c). Ships **dormant** — `api.tubeonai.com` verified a real API host from this box 2026-08-07 (200 root, genuine 404 on a nonsense path, different body hash — not the supercool shell trap), but **no published endpoint docs exist**, so `contract_test()` must pass before it emits a signal. A summary is a *paraphrase*: it can promote a need, it can never back a published claim. |
| **DEC-004** | **Capture the people and companies who commented on the problem** | New `voices` + `voice_mentions` tables, new step **A1d**, qualification (A5) now reads named voices instead of inferring, and new approval-gated launch step **F5b** sells to the people who described the need. Also gives a **distinct-voice** denominator for frequency/severity — five mentions from five people is a market, from one person is one loud person. |

### Blocked on the operator
| ID | What | Why it blocks | Runbook |
|---|---|---|---|
| ⚠️ HT-001 | Telegram forum supergroup + 6 topics + bot | **NO LONGER BLOCKING.** The queue works today via `jpd tasks reply <REF> "<answer>"` — proven live. HT-001 makes it reachable from a phone and enables the Sintra thread. Chat/thread ids are DATA (`jpd telegram configure`), so no redeploy | `runbooks/HT-001` ✅ written |
| ⛔ HT-004 | Products Google Sheet (import the generated CSV) | I cannot create it — the Google Drive connector is unauthenticated and exposes no sheet-creation tool | `runbooks/HT-004` ✅ written |
| 🔴 HT-005 | **Create the new GHL store for JPD** (DEC-002) | **NOW THE #1 BLOCKER.** Phase 1 is built and tested; this is the only thing between it and a real purchase. Verified browser-only — there is no stores API (`/store/store`, `/stores`, `/store/store/list` all 404) | `runbooks/HT-005` ✅ written |
| 🔴 HT-002 | **YouTube Data API v3 key + six channel handles** | **The connector now exists (D-016); only the credential is missing.** Free, ~10 min, no card. Also needs a `handle` or `channel_id` per source in `sources.config` — the connector refuses to resolve from a display name, and a wrong handle returns an EMPTY result set rather than an error, so these must be read off each channel's own URL and not guessed | `runbooks/HT-002` ✅ **written 2026-08-08** |
| 🔴 CAP-001 | **Lift the Anthropic spend cap** (Console → Settings → Limits, and the per-key limit) | Blocks the forge's factual pass, so **no artifact can become `offerable`**. NOT a wait-until-2026-09-01 — the cap is self-imposed and operator-liftable today. Verify with `jpd connectors check anthropic` → `live` | *no runbook — it is two clicks* |
| ⛔ HT-003 | Skool access decision (browser vs manual export) | Highest-intent pain source unavailable. **Skool is the one seeded authority channel D-016 did NOT cover** — it has no public API | *not yet written* |
| ⛔ HT-006 | TubeOnAI credential + endpoint docs (DEC-003) | Connector stays dormant until `contract_test()` passes | *not yet written* |
| 🔴 INPUT-001 | **The products spreadsheet** — not present anywhere on this host | Phase 3 selection uses a 59-row reconstruction (`docs/products-inventory.csv`) instead of your real list | `runbooks/HT-004` |

> The GHL tenant is **co-tenanted** — its 53 products include a hiking-trail/tours business
> unrelated to this work. Verified 2026-08-07. Any "count our products" query must filter.

### Not started
Build phases **7–8** (`docs/02-ARCHITECTURE.md §11`).

### Phase 6 — exit criterion MET
> *"Three artifacts from one need, zero uncited claims."*

**3 artifacts · 3 tiers · 0 uncited · 11,283 words · 36 acceptance tests · all 3 files on the
shared volume and visible from `jarvis_commerce`.**

⚠️ **No artifact is `offerable`** — the factual pass could not run (API budget). The verifier
refused to mark unverifiable claims as supported, which is correct and is the whole point of
having one.

### Phase 5 — exit criterion MET
> *"A dossier with ≥15 live hash-verified evidence rows"* — and zero uncited claims.

| measure | result |
|---|---|
| live AND substantive evidence | **25** (bar ≥15) · 33 domains · 0 unhashed |
| claims | 28 — 21 gap across **12** domains, 7 pricing |
| **uncited claims** | **0** |
| `needs.gap` | backfilled `NULL` → 10.0 |
| Deployed tier | **withheld** — `ghl_payments`/`mailgun` not live. Roadmap + Instructions still sell |

### 🔴 CREDENTIALS NOW WIRED — sourced from Pimlico, not minted
`ollama` · `qdrant` · `anthropic` · `openrouter`. Same keys, same accounts.
~~**11 of 13 connectors are live.**~~ → **10 of 26 live**, re-counted 2026-08-08 from
`jpd connectors`. Values were never printed.

> 🔴 **`openrouter` is NOT wired — this line was wrong.** `JPD_OPENROUTER_KEY` appears in
> exactly one place in the codebase: the `credential_status()` list in `config.py`. There is
> **no OpenRouter connector and no fallback in `forge/build.py:_llm()`**, which calls
> `api.anthropic.com` unconditionally. Verified 2026-08-08. So OpenRouter is **not** an escape
> hatch from the Anthropic cap — that would be new code. Lift the cap (CAP-001) instead.

- **ollama** — `nomic-embed-text`, 768 dims, ~2.4s/call. *This unblocks embedding-based
  clustering, which would fix the phase-4 polysemy limit.* Not yet wired into clustering.
- **qdrant** — ⚠️ its `pimlico_signals` collection belongs to the OTHER platform. JPD must
  create its own and never write to that one.
- **anthropic** — claude-opus-5 / sonnet-5 / haiku-4-5.
  🔴 **Three guessed model names all returned `404 not_found_error`, which reads exactly like
  a bad key. The key was fine.** Ask `/v1/models`; never guess a model name from memory.
- **No you.com key exists anywhere** (checked stack env, running containers, both .env
  backups). B1 uses **DuckDuckGo lite** — verified 200 from this VPS, behind a real
  contract test. Bing and Mojeek are alternates.
  ⚠️ **`runbooks/HT-007` overstates how ready this is.** Two gaps, verified 2026-08-08:
  there is **no `YouComSearch` class** (only `DuckDuckGoSearch` at `research/evidence.py:201`),
  and the runbook's `UPDATE research_params SET value='you_com'` is currently a **no-op** —
  `capture_search()` at `evidence.py:278` hardcodes `DuckDuckGoSearch()` and never reads
  `search_provider`. The row exists; nothing consumes it. Setting the key alone changes nothing.
- **No YouTube key exists anywhere either** — not in the JPD env and not on the Pimlico stack,
  which is where the four above came from. Checked 2026-08-08. This is why D-016's connectors
  could not be probed from this VPS before they were written.

### Phase 4 — exit criterion MET
> *"One need promoted **autonomously** from ≥2 source types."*

| need | source types | signals | voices | severity | score | by |
|---|---|---|---|---|---|---|
| #10 | community + review + search (**3**) | 8 | 5 | 4.50 | 7.56 | `auto` |
| #9 | review + search (2) | 9 | 5 | 4.33 | 7.12 | `auto` |

`gap` is NULL on both — deferred to Phase B, never invented. Pimlico has promoted **zero**.

### 🔴 THE FINDING THAT PROBABLY EXPLAINS PIMLICO'S DEAD FUNNEL
With each source pointed at a **different subject**, the funnel produced **zero** cross-source
clusters — measured at every threshold from 0.30 down to 0.08, where clustering already
over-merges. The vocabularies do not overlap, so nothing corroborates anything.

**More volume does not create corroboration between independently-scattered sources.**
Pimlico accumulated 1,690 signals and promoted nothing; this is very likely why. Cross-source
agreement has to be **arranged**, by pointing sources at a shared problem domain
(migration `006` — and it is config, not code).

### ⚠️ Honest limits — read before trusting the two needs
- **Need #9 is weaker than its 7.12 score suggests.** It merged "automate accounts payable"
  with App Store reviews about *login* failures, because `account` is polysemous and
  *accounts payable* / *create account* stem identically. Lexical clustering cannot tell them
  apart. Embeddings would — blocked on ollama/qdrant credentials (both 401). The A7 operator
  gate exists for exactly this, and auto-promote at 7.0 is arguably too permissive.
- The corpus is **68 admissible signals**. Small, and the calibration reflects it.

### Phase 3 — exit criterion MET, proven live
> *"A deliberately-broken connector goes dormant within one interval."*

`product_hunt` was live; its feed URL was repointed at a non-feed; **one** health check took
it to `dormant` in **0.2s** against a 900s interval. It was then not called at all, and
restoring the URL brought it back to `live` — via a **passing contract test**, not a probe.

**7 connectors live, no credentials required**, covering **5 of 6 source types**
(community ×3, filing, search, review, launch). The cross-source gate needs ≥2 distinct
types, so the funnel can actually promote. First real harvest: **160 signals, 154 stored,
120 voices**.

### 🔴 Facts about sources, verified from this VPS — do not re-derive
| Source | Result |
|---|---|
| hacker_news (Algolia), github_issues, stackoverflow, google_suggest, app_store_reviews, product_hunt | **200, live** |
| **sec_edgar** | **200 — but ONLY with a declared User-Agent** (`"Pimlico Services admin@pimlicoservices.com"`). A generic UA gets **503**, which reads as "their service is down". |
| **reddit** | **403** on both `www.` and `old.` — this datacenter IP is blocked; needs OAuth. Kept as a real connector so it reports *why*, rather than not existing. |
| **indie_hackers** | 🔴 **RESOLVED 2026-08-08 — the feed is GONE, not moved. There is no correct URL to find.** Ten paths (`/feed.xml` `/feed` `/rss` `/rss.xml` `/atom.xml` `/index.xml` `/posts/feed.xml` `/products/feed.xml`, apex) **all return 200 with HTML**; feedburner 404s. No `rel=alternate` tag; no JSON API (`/api/posts` `/api/v1/posts` `/api/feed` `/graphql` `/_next/data`). **Retired to dormant-with-a-reason** (D-017). Needs a new *transport*, not a new URL. |
| **app_store_reviews — the empty-feed rule** | 🔴 **Most App Store apps have NO working review RSS, regardless of size.** Screened ~95 candidates 2026-08-08: **SAP Concur (1.1M ratings), Expensify, BILL AP & AR, Brex, Dext, Melio, NetSuite and Invoice Simple ALL return the empty envelope**, repeatedly. So an empty feed is the NORM, not evidence that a source died — which is the trap that produced the wrong retirement call below. **Never add an app id without confirming its feed returns entries on several probes.** |
| **app_store_reviews** | ✅ **LIVE. Do NOT retire it** — an earlier entry in this file called it permanently broken and was WRONG; see the correction below. It flapped dormant during the v39 sweep because Apple's RSS served empty envelopes in a transient window. Re-probed later the same hour: **QuickBooks `584606479` → 50 entries, 33 of them 1–3★**; Zoho `710446064` → 50 entries, 16 low. `jpd connectors harvest` → **8 admissible signals**. Four of the six configured apps (Xero, FreshBooks, Wave, Invoice2go) do return empty US feeds consistently — that is a **config** question about app ids, not a dead source. |
| ollama / qdrant | reachable via nginx but **401** — need API keys. |

> ✅ **This is the C3 dormancy machine doing exactly the job it was built for, on the exact
> source that motivated it.** `app_store_reviews` is one of the three Pimlico sources that
> *"returned 0 items every day with `dormant: []`"* (`connectors/base.py`). Here the contract
> test refused a clean parse yielding **zero** signals, and the connector walked itself to
> dormant with a reason — no human noticed the source died, the system did.
> ⚠️ The corollary is less comfortable: **health is only as fresh as the last check.** It read
> `live` at 06:55 today on the strength of an older passing contract test, and only flipped
> when the v39 roll triggered a sweep. Its `evidence` note already said
> *"returns 29 on demand but 0 at harvest in Pimlico — unexplained"*, so this may have been
> broken for some time. **Do not read `live` as "checked recently".**

> 🔴 **A 200 is not evidence when every path returns one.** Indie Hackers is a single-page app:
> `/rss` and `/this-path-is-nonsense-9f3a2b` return **byte-identical** bodies (sha `f1d0a999…`,
> 22,115 bytes). Any "is it there?" check built on status codes alone would have reported nine
> working feeds. **Hash a real path against a nonsense path** — the same test DEC-003 used to
> confirm `api.tubeonai.com` was *not* this trap. That check is now the difference between two
> connectors we understand and two we would have kept guessing about.

### Phase 2 — exit criterion MET, and how it was proven
> *"a real human task blocks and unblocks a run"*

In the suite: a real step, through the real engine, against a real database — blocks
(`run.status='blocked_on_human'`), is answered, and the **same step re-run** succeeds with the
typed value. Resuming is just running the step again; there is no separate resume path and no
callback to lose.

Live on the deployed stack: a task was created, a bad reply was **rejected and re-asked**
without persisting, a good reply resolved it.

**C7 proven concretely:** `jarvis_core` scaled to **0/0**, and with core completely down the
console still served `/ready` and `/tasks`, and a decision task was **created and answered**.

### Phase 1 — what is and is not proven
**Proven end-to-end**, against a real database with only the *provider* stubbed (signature
enforcement, amount checking, idempotency, entitlement, artifact existence, token minting,
redemption and notification are all production code):

> three buyer journeys · upgrade delta · replay · underpayment · overpayment · bad signature ·
> unknown product · missing amount · non-live offer · missing artifact · empty artifact ·
> partial delivery · revoked entitlement · token hashing · download limits · expiry ·
> revocation · the artifact sweep · attribution

**Not proven: a real purchase.** That is the actual exit criterion and it needs **HT-005**.
Run `jpd commerce test-ladder --base-minor 100` once the store exists — it creates a real
€1/€3.50/€12.50 ladder and refuses to run while the provider is dormant.

### GHL facts, verified from this host 2026-08-07 — do not re-derive
- **No stores API.** `/store/store`, `/stores`, `/store/store/list` → 404. HT-005 is browser-only.
- **Store membership IS machine-readable** via `excludedStoreIds` on each product (43 products
  exclude one store, 9 the other). This is the filterable namespace DEC-002 wanted — better than
  the name-guessing the earlier note assumed.
- **Payments work**: a live product page renders a real Stripe checkout.
- ⚠️ **`/payments/integrations/provider/whitelabel` returning `providers: []` is NOT evidence
  that payments are disconnected** — it lists *whitelabel* providers only. An earlier session
  drew exactly that false conclusion. Judge by the rendered checkout.
- **httpx is required.** urllib's user-agent trips Cloudflare error 1010; every call 403s.

### What is running right now
| | |
|---|---|
| Stack | `jarvis` (Swarm) — `jarvis_postgres` 1/1 · `jarvis_redis` 1/1 · `jarvis_core` 1/1 · `jarvis_commerce` 1/1 · `jarvis_console` 1/1 |
| Images | core `jarvis/core:v39` · console `jarvis/core:v39` · **commerce `jarvis/core:v4`, pinned separately** — all three verified by image ID 2026-08-08. Core/console share `65f565024f5a`; commerce `5f716151b064`. C5 demonstrated twice: core moved v36→v37→v38 while commerce never moved. ⚠️ `v37` and `v38` are the SAME image ID — identical source, rebuilt only to exercise `deploy.sh` |
| Ports | 5632 pg (host) · 6581 redis (host) · 8900 core (**ingress**) — **all blocked** from a TEST-NET-3 source, control 8000 = 200. **8904 commerce and 8905 console are deliberately UNPUBLISHED** |
| Schema | 40 tables, migrations `001_core` + `002_seed` + `003_commerce` + `004_console`, sha256-tracked, drift-refusing |
| Seeded | 19 sources · 26 connectors · 6 gate thresholds · 3 pricing tiers · 6 telegram streams · 3 notification channels · 10 jobs |
| Connectors | **10 live / 22 dormant** (32 registered), verified on v39. Live: `hacker_news` `github_issues` `stackoverflow` `sec_edgar` `google_suggest` `app_store_reviews` `product_hunt` `duckduckgo` `ollama` `qdrant` |
| Tests | **272 passing, 0 failed, 0 errors** in 104.87s — run 2026-08-08 inside **`jarvis/core:v39`, the deployed image**, against the real migrated schema in `jarvis_test`. `integration` 133 (10 files) · `unit` 99 (9) · `journey` 24 (1) · `stages` 16 (3). Baseline this morning was 245; the 27 added are D-016 (17), D-017 (2) and D-019 (8) |
| Exit criteria | phase 0 ✅ · phase 1 ⏳ real purchase blocked on HT-005 · phases 2–6 ✅ **MET** (phase 6's factual pass pending CAP-001) |

> ⚠️ **This table has gone stale twice.** It read `v6` / `202 tests` while the stack ran `v36`
> with 245. Anything here is a claim about a *previous* session's stack — re-derive it with
> `docker service ls --filter name=jarvis` and `jpd connectors` before trusting a number.

**Two different protections, do not conflate them.** `commerce` is separately *pinned*
(`platform/docker/COMMERCE_VERSION`) so feature work cannot roll the money path — editing it
runs the journey tests first, blocking. `console` tracks core's version; what protects it is
being a *separate process*, proven by scaling core to 0.

**`jpd` prefers the CONSOLE container**, then core, then commerce — preferring core would mean
the CLI is broken exactly when core is.

**Rolling the money path is deliberate.** `commerce` moves only when
`platform/docker/COMMERCE_VERSION` is edited, and editing it makes `deploy.sh` run the
journey tests **first, blocking**. Core went v4→v5 while commerce stayed on v4 — the C5
property is demonstrated, not asserted.

Operator entry point: **`/opt/jarvis/bin/jpd`** (execs into the running container, so the
operator uses the exact binary the service uses — Pimlico had host scripts that drifted).
Useful: `jpd resume` · `jpd doctor` · `jpd verify --last` · `jpd connectors` · `jpd steps`.

---

## Carried facts — verified live 2026-08-07, do not re-derive

- Pimlico is **healthy at the infrastructure tier**: 15/15 swarm services, 8/8 Prometheus targets,
  0 active alerts, disk 19%, all Docker-published ports blocked from a TEST-NET-3 source with a
  valid control port.
- ✅ **All four Pimlico defects are FIXED as of 2026-08-07** (hermes v19, browser-agent v3 —
  see `/opt/ops/checkpoints/CHECKPOINT.md` §4.40). The LinkedIn publishing is **stopped**, the
  n8n watcher **self-heals a wedged cursor** (proven by re-wedging it to 9999 and watching it
  clamp to 34), and the scan gauge returns **one value on 12/12 scrapes** where it previously
  alternated across a 1.5-day gap.
- ⚠️ **Correction to an earlier claim in this file:** the "3 zero-yield sources" are **not dead** —
  called directly they return 1, 2 and 29 signals. The real defect is that `dormant` is a
  *hand-set registry flag*, not a computed health state, so a source returning 0 forever can never
  be flagged. **Deliberately left open in Pimlico** (building a dormancy engine there is scope
  creep); it is JPD constraint **C3** and is designed in from the start.
- ⚠️ **`app_store_reviews` returns 29 signals on demand but 0 at the 05:30 harvest.** Unexplained,
  not investigated. Worth a look before trusting that source's contribution to any gate.
- 🔴 **browser-agent's Dockerfile no longer builds** — `python:3.12-slim` moved to a Debian where
  `libasound2` was renamed, apt exits 100. v3 was shipped as a hotfix layer
  (`Dockerfile.hotfix`, `FROM pimlico/browser-agent:v2` + `COPY src/main.py`). The real Dockerfile
  needs repair; do not let the hotfix layer become permanent.
- 🔴 **A syntax check is not an import check.** `py_compile` passed on a hermes change that used
  `Optional` with no `typing` import and no `from __future__ import annotations` — it would have
  raised `NameError` at import and crash-looped the container. An AST import-name check caught it.
  Use one before every deploy.
- **Only 6 of 34** registry integrations have credentials: `content360, getlead, instantly,
  success_ai, supercool, thoughtly`.
- **~two-thirds of the owned portfolio has no API.** This is why `kind="human"` is a first-class
  connector and not a workaround.
- **Sintra is Cloudflare-blocked from this VPS.** It is a human connector in JPD. Do not attempt
  to automate it headlessly — that path is what produced the LinkedIn incident.

---

## Why the architecture is shaped this way

Three decisions carry most of the weight. If a future session wants to change one, read this
first.

**1. The three tiers are the pipeline's own artifacts, not three separate products.**
Phase C produces the Roadmap, D the Instructions, E the Deployed system — each a superset of the
last. Three price points at zero marginal cost, a natural upgrade ladder, and — critically — if
the build fails, two complete products still exist and still sell. Pimlico's all-or-nothing model
turned every build failure into zero revenue.

**2. Commerce is built first and deployed separately.**
Pimlico built discovery → generation → marketing → *(and never finished delivery)*. It has nine
live products, real PDFs, working sales pages, working checkout links — and **zero orders, ever**,
with nowhere in the schema to record one if it happened. JPD inverts this: `offers/orders/
entitlements/fulfilments` exist before any generation code, and a real purchase of each tier is
the exit criterion for build phase 1.

**3. Failure detection is tested like a feature.**
Pimlico's roadmap correctly identified "nothing reports failure" as the root cause of every other
defect, built eleven alert rules to fix it — and today four of its detectors are silently broken.
An untested detector is not a detector. Every alert in JPD ships with a synthetic-failure test
that trips it on a schedule and asserts it fired, plus a meta-alert for any rule not verified in
10 days.

---

## Lessons earned during phase 0 — do not re-learn these

1. **Assert ownership where it can be taken from you, not where you wrote it.**
   The lease guard was implemented exactly as designed (`WHERE lease_owner = $1` on the step
   row) and was **useless**: the step writes that column itself, so it always matched and a
   killed run could still record a success. Only checking the *run's* lease works. A test
   caught it; code review would not have.
2. **A syntax check is not an import check** — carried from Pimlico, enforced in the
   Dockerfile as a real `import` of every module at build time.
3. **"Converged" is not "running the new code."** With `order: start-first` the OLD task
   satisfies a task-count check instantly. Poll for the new **image ID** to be the one
   running, and warn if any container is still on the old one.
4. **Host-mode ports cannot do a start-first roll on a single node.** The old task holds the
   port. Ingress mode or stop-first — pick deliberately, per service.
5. **A service that refuses an unmigrated schema cannot be the thing that migrates it.**
   Migration is a one-shot container, which also keeps DDL away from racing replicas.
6. **A generated file must refuse to eat a hand-written one.** `render` now demands a
   preservation marker. This file is the reason.

## Lessons earned during phase 1

7. **A deploy-gating test suite must be reproducible from nothing.** A test that legitimately
   deleted a seeded `pricing_policy` row left the table with two rows; the next run's "snapshot
   the seeded tables" faithfully captured the damage and restored it before every test. Four
   tests failed for reasons unrelated to their own code. The suite now **drops and rebuilds the
   schema from the migrations every session**.
8. **`/ready` returning 200 does not mean the service is healthy.** `commerce` inherited the
   image's `HEALTHCHECK`, which probes core's port `:8900`; it listens on `:8904`. The process
   worked perfectly, Docker declared it unhealthy forever, and swarm restart-looped it — which
   reads as a crash loop and is not one. **Any service reusing an image on a different port must
   override the healthcheck**, and the deploy must assert the *replica count*, not just `/ready`.
9. **An error message should name the cause, not the symptom.** A downgrade request fell through
   to the delta arithmetic and reported "delta must be positive". The real problem was that the
   ladder only moves upward, and now it says so.
10. **Never let a runbook cite a command that does not exist.** HT-005 referenced
    `jpd commerce test-ladder` before it was written. It exists now, is tested against an empty
    database, and **refuses while the provider is dormant** while naming the outstanding task.

## Lessons earned during phase 2

11. **Moving a failure is not removing it.** Making Sintra a human connector stopped the
    platform from publishing its own error text — but the operator can now paste whatever the
    Sintra UI showed them, which is the *same string*. The reply schema had to reject
    `[Automation failed`, `Traceback`, `Page.goto:` on the human path too. When you relocate a
    failure across a boundary, re-check every gate it used to pass through.
12. **A pre-rendered card and its DB row must agree on the reference.** `human.sintra` printed
    `VERIFY jpd tasks show SIN-ABC123` while `tasks.create` generated `JPD-F0DC60`. The
    operator would have run a command that finds nothing, and the symptom points at the task
    store rather than at the card builder.
13. **Seeded-table pollution recurs once per phase.** `pricing_policy` in phase 1,
    `telegram_streams` in phase 2. Any table seeded by a migration and mutated by a test must
    be in the conftest snapshot/restore list. Expect to add one more each phase.
14. **An operator surface with one route in has a single point of failure.** The CLI wrapper
    originally exec'd into `core`, so the CLI died exactly when core did. It now prefers
    `console`.
15. 🔴 **Fixing one thing broke another, silently — and it reported success.** Making the
    wrapper prefer `console` meant `jpd checkpoint render` ran in a container with no
    `/opt/jarvis/checkpoints` bind mount. It wrote a 2 KB file into the container's ephemeral
    filesystem and printed *"wrote /opt/jarvis/checkpoints/CHECKPOINT.md … preserved"*. The
    host file was untouched and nothing said so. Caught only by noticing the byte count was
    absurd for a file that should be ~23 KB.
    **Every service that can serve the CLI now carries the mount.** The general lesson: when a
    command writes to a path, "it reported success" is not evidence the bytes landed where you
    think — check the destination, not the return code.

## Lessons earned during phase 3

16. **A probe and a contract test are genuinely different checks, and I proved it by being
    wrong.** I wrote the `indie_hackers` connector after seeing `200` and `316KB` and
    assuming RSS. It serves `text/html`. The probe says healthy; the contract test says the
    shape is wrong. A probe-only health check would have called it fine for ever — which is
    exactly how Pimlico's three "dead" sources sat at zero yield with `dormant: []`.
17. **`degraded` must still be called, or it is a one-way trap.** Gating harvest on
    `state != "live"` meant a connector at three zero-yields stopped being called, so its
    streak could never reach five (never dormant) and never reset (never recovered). It
    would sit in degraded for ever — the invisible limbo C3 exists to abolish.
18. 🔴 **An alert that always fires is not an alert.** `dormant > 0` fired permanently
    because **17 of 24** connectors are dormant by design, waiting on credentials. Replaced
    with `ConnectorRegressed` — connectors we have actually exercised that are not live.
    Measured: **17 → 2**, both real. Pimlico ignored its own monitoring for this reason.
19. **A synthetic that leaves residue trips the alert it was testing.** The
    `UndeliveredPaidOrders` synthetic's `DELETE FROM needs` looked like it would cascade the
    graph away — but the money-path FKs are `ON DELETE RESTRICT` on purpose, so cleanup
    stopped dead and left a fake unpaid buyer behind. Cleanup must unwind in dependency order.
20. **Declare who you are to the SEC.** `sec.gov` gives **503** to a generic User-Agent and
    **200** to a declared name + contact. A 503 reads as their outage, not our misuse.
21. **`jpd alerts render` writes to STDOUT, not a path** — and that is the class-level fix
    for lesson 15, not another bind mount. stdout cannot land in the wrong place: the host's
    shell owns the redirect. (Lesson 15 recurred here first, exactly as predicted.)

## Lessons earned during phase 4

22. 🔴 **Cross-source corroboration must be ARRANGED, not hoped for.** Sources pointed at
    different subjects produce zero overlap at ANY similarity threshold. This is almost
    certainly why Pimlico's 1,690 signals promoted nothing — the fix is topic alignment in
    `sources.config`, not more volume and not a looser gate.
23. **Look at the data before designing the algorithm.** Reading the first 155 stored signals
    found four connector defects in ten minutes: character-split SEC concepts, App Store
    pointed at WhatsApp and Starbucks, one source at 80% of the corpus, and filings from
    2001. None would have been visible from the code.
24. **Jaccard is the wrong metric for mixed-length text.** It normalises by the union, so a
    60-word review and a 6-word query score near zero on 2 shared terms. Cosine is better and
    still symmetric. The question is *containment* — normalise by the smaller document.
25. **Tokenisation before threshold tuning.** `reconcile` / `reconciliation` shared zero
    tokens. It presented as a threshold problem for three rounds of calibration and was a
    stemming problem.
26. **Never guess an external id.** Two of my "verified" App Store ids returned NOT FOUND —
    the same failure mode that put WhatsApp in a B2B tooling source. Look them up.
27. **A gate must be satisfiable by every source it applies to.** `distinct_voices` was
    unsatisfiable for authorless sources; Google Suggest has no author by construction.
28. **Calibrate in a migration, not with an UPDATE.** The tuned threshold lived only in
    production; the test database rebuilt from migrations still had the old value, so tests
    and production behaved differently. Caught by a test, not by review.
29. **A stage test must not depend on a production-tuned parameter.** It verifies the
    mechanism; calibration is verified by measurement against real data. Coupling them broke
    the same test twice for reasons unrelated to its subject.

## Lessons earned during phase 5

30. 🔴 **Fetched-and-hashed is not evidence.** The first dossier passed with 21 "live
    hash-verified" rows, of which four were `"Connecting to the iTunes Store."`, three were
    Google result pages and one was a Cloudflare interstitial. All genuinely retrieved, none
    evidence of anything — **the same species of lie as Pimlico reporting `processed=4` when
    all four prompts had failed.** Compute a `substantive` flag at capture time and count only
    that. When the honest count then failed at 8/15, the fix was to capture MORE, not to
    redefine evidence.
31. **Never guess a model name.** Three plausible Anthropic model ids returned
    `404 not_found_error` — indistinguishable from a dead key. `/v1/models` returned 200 and
    listed entirely different names. Ask the API what it serves.
32. **Repetition from one domain is not corroboration.** 21 gap claims from 2 domains scored
    a perfect 10.0. Cap claims per domain and score on distinct domains.
33. **Distinguish DEAD citations from CHANGED ones.** Re-verification found 2 dead and 12
    changed. A live link whose bytes no longer match the quote beside it is the more dangerous
    failure, and collapsing both into "unverified" hides it.
34. **Any container that can serve the CLI needs everything the CLI touches.** `jpd` prefers
    the console container; console had no credentials, so `jpd connectors check` failed its
    probes and walked healthy connectors toward dormant — the operator tool corrupting the
    state it exists to report.
35. **A partial unique index needs its predicate repeated in `ON CONFLICT`.** Otherwise
    postgres raises "no unique or exclusion constraint matching" and it reads like a missing
    index.

## Lessons earned during phase 6

36. 🔴 **Parse LLM responses by block TYPE, never by index.** `claude-opus-5` returns 200 with
    a thinking block first; `content[0]["text"]` raised KeyError, was swallowed as None, and
    the forge produced **zero sections in 691 seconds** having paid for every call.
37. 🔴 **A long step outlives its lease unless something renews it.** `forge.generate` ran
    580s against a 120s TTL and the NEXT step died. **This is the exact failure this codebase
    quotes Pimlico for, reproduced.** The engine now heartbeats at TTL/3 during a step.
38. **Expensive output must be durable the moment it exists.** 696s of paid generation lived
    only in `ctx.data`; the next step failed, and the re-run hit the idempotency cache and
    found nothing. Write drafts to disk as they are produced.
39. **A single-valued FK cannot express "cited by all three tiers".** Packaging made each tier
    steal the previous one's citations, so two artifacts were marked verified **vacuously**
    and declared offerable. An artifact citing nothing is UNVERIFIED, not verified.
40. **A verifier that withholds good work is as damaging as one that passes bad work.** Naive
    substring matching flagged legitimate prose (`"custom quote" placeholders`) and a script
    the buyer reads to their bank (`"I cannot access the account"`). Anchor the patterns.
41. 🔴 **THIRD OCCURRENCE: the CLI writes where it runs.** `jpd forge run` executes in the
    console container and wrote 12,759 words of product into an ephemeral filesystem.
    After `checkpoint render` and `alerts render`. **Any container that can serve the CLI
    needs every mount and every credential the CLI touches.**
42. **When a check cannot run, the answer is NOT "pass".** The Anthropic budget ran out and
    every fact-check failed; the verifier marked those claims unsupported and refused to make
    anything offerable. Defaulting to "supported" on a failed check is how a verifier comes to
    approve everything it was built to catch.

---

## Lessons earned building the `authority` connector (D-016, 2026-08-08)

43. 🔴 **Read the error body, not the status code.** The Anthropic 400 says *"your **specified**
    API usage limits"* — a **self-imposed** Console spend cap, liftable in two clicks. The
    previous entry recorded it as "regains access 2026-09-01" and would have idled the build
    for three weeks. `probe` passing while only `messages` 400s was the tell: the key was fine
    the whole time.
44. 🔴 **A connector must refuse to guess its own identity.** Resolving a YouTube channel from
    a display name would sometimes return the wrong channel — which still yields signals, and
    every gate downstream believes them. **A wrong-data connector is worse than a dormant
    one**, because dormancy is visible and wrong data is not. Same root cause as the three
    guessed Anthropic model names that 404'd and read like a dead key.
45. **A credential that N connectors share needs its own health connector.** One key backs six
    `yt_*` sources; without `youtube_data_v3` a dead key produces six *channel-shaped* errors
    and nothing anywhere says *the key is the problem*.
46. **Quota is an architectural constraint, not a runtime detail.** `search.list` costs 100
    units of 10,000/day and `playlistItems.list` costs 1. The naive design exhausts the quota
    in sixteen harvests and then **presents as a dead connector** — `quotaExceeded` is a 403,
    the same status as a bad key. Resolve once, cache the id as data, take the 1-unit path.
47. **One status code, four different fixes.** `quotaExceeded`, `keyInvalid`,
    `accessNotConfigured` and `ipRefererBlocked` are all **403**. Surface the provider's
    machine-readable `reason` or the next session debugs the wrong thing.
48. 🔴 **A credential in a query parameter leaks through the error path.** `detail` is persisted
    to `connector_health` and printed by `jpd connectors`, so a message built from the URL
    writes the API key to the database and the terminal. Build messages from the *path*. The
    test asserts the key really is in the outgoing URL **and** absent from the detail —
    asserting only the absence would pass against a connector that never sent the key.
49. **"Tests green" is not "verified against the real API."** Every other source was probed
    from this VPS before it was written; this one could not be, because no key exists here.
    That is a weaker guarantee and it belongs in the docstring, the test file and this
    checkpoint — otherwise a future session reads a green suite as proof of something it
    never checked.
50. **Deleting a stale assertion is part of the change.** `orphans` asserted `yt_alex_hormozi`
    had no implementation. Re-point such a test at something still true **and add the inverse**,
    so the next regression fails loudly instead of six sources quietly returning nothing.
51. 🔴 **A 200 is not evidence when every path returns one** (D-017). Nine Indie Hackers feed
    candidates "worked" by status code; the SPA serves a byte-identical shell for a nonsense
    path. **Hash a real path against a nonsense path before believing a status code** — the
    test DEC-003 already used on `api.tubeonai.com`. It should be the default for any new
    HTTP source, not a thing remembered twice a year.
52. **Investigate before re-pointing a broken URL.** The obvious fix was a new `feed_url`, and
    it would have failed the same way against a page that no longer exists — burning the next
    session too. The error message a dead connector leaves behind is the deliverable: *"the
    feed was removed, this needs a new transport, not a new URL"* ends the search, where *"not
    well-formed XML"* restarts it.

53. ✅ **FIXED — `deploy.sh` verified `console` by replica count alone** (D-018). Its own header
    rule is *"verify by IMAGE ID"*, and it did that for `core` and `commerce` via `converge()`
    while console got the `1/1` check only — which the OLD task satisfies under
    `order: start-first`, the precise failure the comment above `converge()` warns about.
    Console was also **not readiness-checked at all**. It happened to be correct on the v37
    roll (verified by hand), but the script would not have known. **The CLI runs in console**,
    so a console that silently failed to roll makes every later `jpd` command report the OLD
    code's behaviour — while `docker service ls` shows the new tag and every count reads 1/1.
    **A verification step that cannot fail is not a verification step**, and the two services
    that were fully checked are exactly the two where the omission would have been noticed.
54. 🔴 **An image-ID convergence check passes VACUOUSLY across a no-op rebuild.** Rebuilding
    unchanged source gives the new tag the **same image ID** — `v37` and `v38` are both
    `65f565024f5ae`. So on that roll `converge` could not distinguish "rolled to v38" from
    "still on v37"; it passed without discriminating. The roll still proved what it was for
    (the script *executes* — both new blocks ran under `set -euo pipefail`), but **do not
    read a green no-op deploy as proof the convergence check works.** Prove discrimination
    against a genuinely different image (`v36`), as was done here.
    > Same shape as lesson 39: two artifacts were marked factually verified because their
    > citations had been taken away, not because anything was checked. **A check that had
    > nothing to compare is not a check that passed.** This is the second instance; expect a
    > third somewhere else and look for it. → **Found. Lesson 55.**
55. 🔴 **THIRD INSTANCE, and the worst — `all([])` promoted anything** (D-019, §24). The
    promotion verdict was `all(r.passed for r in self.results)`, and gates absent from
    `gate_thresholds WHERE enabled` were skipped **silently**. Disable the rows and a cluster
    failing 6/6 gates promoted, `failed_gates=[]`, log reading `passed=True`.
    **Three properties made it lethal rather than merely wrong:**
    (a) **the supported operation was the destructive one** — retuning gates is data by
    design, so tuning and deleting looked identical; (b) **the partial case was quieter than
    the total one** — dropping `cross_source` alone silently ends "authority cannot
    self-corroborate", which is the rule that stops the system building a product because one
    influencer was persuasive; (c) **it was latent**, so no amount of watching production
    would have surfaced it.
    🔴 **The generalisation worth keeping: audit the shape, not the instance.** Lessons 39 and
    54 were each written as a one-off. Going looking *for the pattern* found a live defect in
    the promotion path in under an hour. **Two of three instances were caught by the system;
    this one was caught only by deliberately hunting it.**
56. **Assert the negatives, or the config path is untested.** This survived because every
    test asserted good input passes; **nothing asserted a broken gate CONFIG fails.** The new
    suite tests only negatives — empty verdict, six disabled, one disabled, the error's
    contents, and that a config error is never downgraded to "did not qualify".
    ⚠️ Still open by choice: `discovery.qualify` (`considered >= 0`), `discovery.promote`
    (`decided >= 0`) and `research.observe` (`observations >= 0`) are **tautologies** — they
    cannot fail. Deliberate, but they are acceptance predicates that verify nothing.
57. 🔴 **ABSENCE OF CONTENT IS NOT EVIDENCE OF A DEAD ENDPOINT. ABSENCE OF STRUCTURE IS.**
    I declared `app_store_reviews` permanently broken on a single snapshot — 200, valid
    envelope, no `entry` key, reproduced across all six app ids — and wrote it into this file
    as *"the endpoint serving empty feeds"*. **It was a transient window.** Re-probed within
    the hour: QuickBooks returned **50 entries, 33 of them 1–3★**, and a harvest produced 8
    admissible signals. Had the retirement gone ahead it would have deleted a working source —
    the **only** `review` source.
    **Contrast with `indie_hackers` (D-017), which was diagnosed correctly.** There the proof
    was STRUCTURAL: `/rss` and `/this-path-is-nonsense` returned **byte-identical** bodies, so
    the endpoint provably could not discriminate paths. A structural invariant cannot be
    transient. Here the evidence was the **absence of content**, which is precisely what a
    temporary outage looks like — and content comes back.
    **The rule: to declare a source dead, find something structurally impossible, or observe
    the failure repeatedly over time. One empty response is a sample of one.** Note the
    connector's own `evidence` note had said *"returns 29 on demand but 0 at harvest —
    unexplained"* since Pimlico; that was this same flapping, and reading it as confirmation
    rather than as a warning about sampling is exactly how the wrong conclusion got support.
58. **A dormancy flap is not a death.** `app_store_reviews` went dormant on one failed
    contract test, which is the state machine working — but `state == dormant` means *"the
    last check failed"*, **not** *"this source is gone"*. Re-check before acting on it. The
    two need different responses and the state alone cannot tell them apart.
59. ⚠️ **App Store reviews are mostly complaints about the APP, not about the business
    process** (D-020). Read before weighting this source. Sampling the actual 1–3★ text:
    Ramp and Relay are dominated by *"stuck at login"*; the useful ones name a workflow —
    *"unable to produce a quarterly report so I can file with the state"* (Roll by ADP),
    *"no reporting data on numbers sold, categories, volume by type"* (SwipeSimple),
    *"price increase for my subscription"* and an explicit feature ask about client
    signatures (Joist). **Select app ids by reading the reviews, not by counting them** —
    TurboTax offered 47 low-star and is a consumer product; the connector's own history is
    shipping WhatsApp reviews labelled "Slack-ish". Severity scoring cannot tell
    *"the app crashes"* from *"reconciling invoices costs me six hours a week"*, and only
    the second is a product opportunity.
60. ⚠️ **A green suite says nothing about the DATA half of this system, and that half is
    large.** 272 tests cover the code; they do **not** cover `sources.config` app ids,
    `gate_thresholds` values, `research_params`, price ratios, or telegram stream ids — all
    of which are deliberately tunable as data, and all of which change behaviour. D-020
    doubled the yield of a live source and **no test noticed, because there was nothing to
    notice.** Two consequences worth holding:
    (a) after a data change, the verification is a **live run** (`jpd connectors harvest`,
    `jpd discover census`), never the suite;
    (b) the design that makes tuning cheap also makes it **untested by construction** — which
    is exactly how D-019 happened, where disabling a gate row silently removed it from every
    verdict. **Where data can break an invariant, assert the invariant in a test**, as
    `test_gate_config.py` now does for the gate set.
---

## Next actions, in order

> ⚠️ **Rewritten 2026-08-08.** The previous list still had phases 5 and 6 as work to do after
> both had been built and recorded as MET in the same file. A stale next-actions list is worse
> than none — it sends the next session to redo finished work.

1. 🔴 **Operator: `HT-005`** — create the JPD store in GHL. ~10 min, browser-only. **The single
   thing between a fully-built money path and a real sale.** Runbook written, with verification
   commands. Unblocks `ghl` + `ghl_payments`.
2. 🔴 **Operator: `CAP-001`** — lift the Anthropic spend cap in the Console. **Two clicks, not a
   three-week wait.** Then `jpd forge run 13` completes the factual pass and an artifact can
   finally become `offerable` — which is the last unproven link in the whole chain.
3. 🔴 **Operator: `HT-002`** — YouTube key (free) + six channel handles read off each channel's
   own URL. **The code is deployed and waiting** (v37); the key is the only thing missing.
   No redeploy needed — the key is `docker service update --env-add` on `core` **and**
   `console`, and the handles are an `UPDATE` on `sources.config`. Then
   `jpd connectors check youtube_data_v3` → expect `live`.
4. **Operator:** `HT-001` (Telegram forum). ~10 min. **No longer blocking** — the queue works
   via `jpd tasks reply` — but it enables the Sintra thread and phone-reachable approvals.
5. **Operator:** supply the products spreadsheet (`INPUT-001`); import the Sheet (`HT-004`).
6. ✅ **DONE — the modified `deploy.sh` was exercised by a real roll to `v38`** (2026-08-08).
   Both new blocks executed in sequence under `set -euo pipefail`; console converged and
   answered `/ready` on 8905. See the ⚠️ note under D-018 about what that run did **not**
   prove.
7. ✅ **DONE — `indie_hackers` investigated and retired** (D-017). There was no URL to fix; the
   feed is gone. **Do not re-point it at another path** — ten were probed and all serve HTML.
   If the source is wanted back it needs a **new transport** (browser connector, or their
   newsletter), which is a scoping decision, not a bug fix.
8. ✅ **DONE — `app_store_reviews` app ids retuned** (D-020). Four dead ids replaced; **yield
   doubled, 8 → 16 admissible signals**. ⚠️ Read the content caveat in D-020 before weighting
   this source: App Store reviews skew toward *mobile-app* complaints, and only some of them
   are business-process pain.
9. **Use the embeddings.** ollama/qdrant are live but `discovery/cluster.py` is still lexical
   overlap. Swapping it to `nomic-embed-text` vectors is what fixes the phase-4 polysemy limit
   (`accounts payable` vs `create account`).
10. **Tighten the `substantive` gate.** The forge's verifier, while it still had budget,
   produced good rejections that all pointed upstream — *"only site navigation"*, *"almost
   entirely CSS/boilerplate"*. Evidence quality is a better use of effort than more generation.
11. **Build phase 7 — market.** Not started (`docs/02-ARCHITECTURE.md §11`).

> **Do not** chase `reddit` (403 to this datacenter IP, needs OAuth **and** new code, 85
> consecutive failures), `google_trends`, `skool` or `stripe`. They are correctly dormant.
> Realistic ceiling is **10 live → 15 or 16**, not 26 — several seeded rows describe things
> with no API to connect to.

### ✅ The observability gap is CLOSED
Prometheus + Alertmanager on `jarvis_monitoring`, **4/4 targets up**, business metrics being
read. **11 alert rules, 10 with a synthetic test that fires.** Rules are generated by
`jpd alerts render` so they can be diffed — Pimlico's exist only as a docker config with no
source file.

`ServiceDown` is the one rule with no synthetic: proving it means stopping a service in
production. It is recorded `never_run` and **`AlertNeverTripped` correctly flags it** — the
system reports the truth about its own coverage rather than hiding the hole.

⚠️ **Known transient, not a defect:** for ~5 min after a roll Prometheus keeps the old
container's series, so alerts briefly double-count. The `for:` durations (30m/1h) mean it
never pages. Verified: duplicates expired after ~190s, steady state clean.

### Also carried forward
- **No connector is live**, so every notification channel is dormant by design. A buyer
  delivery today records `skipped_dormant` — counted as **owed**, visible, never silent.
- **Ports 8904 and 8905 are unpublished.** Going live for the GHL webhook needs an nginx vhost
  + certbot, the shared secret on both sides, then a netns re-probe with a control port.
- `jpd steps` is still empty by design — the engine ships before the steps.

Each build phase ends with: a checkpoint written, the architecture docs updated, and a green
regression suite. None of the three is optional.
