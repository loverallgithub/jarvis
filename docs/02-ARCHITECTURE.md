# 02 — JarvisProductDevelopment: Optimal Architecture

> Version 0.1, 2026-08-07. Satisfies constraints **C1–C8** from `00-ANALYSIS.md`.
> Every design choice below cites the constraint it discharges. A choice with no citation is
> either infrastructure carried over from Pimlico (§A of the analysis) or a defect.

---

## 1. Shape of the system

```
                            ┌───────────────────────────────────────────┐
                            │        TELEGRAM  (forum supergroup)       │
                            │  #decisions  #human-tasks  #sintra        │
                            │  #discoveries  #revenue  #alerts          │
                            └──────────────────▲────────────────────────┘
                                               │
   ┌───────────────────────────────────────────┴────────────────────────────────┐
   │  jarvis-console        operator surface — deployed independently            │
   │  Telegram bot · human-task board · decision cards · approvals               │
   │  ‼ survives a jarvis-core outage: you can always see and act        (C7)    │
   └───────────────────────────────▲────────────────────────────────────────────┘
                                   │
   ┌───────────────────────────────┴────────────────────────────────────────────┐
   │  jarvis-core                  the factory                                   │
   │                                                                             │
   │   discovery/   research/   forge/   market/                                 │
   │   ─ sources    ─ capture   ─ roadmap  ─ copy                                │
   │   ─ funnel     ─ compete   ─ outline  ─ media                               │
   │   ─ gates      ─ ground    ─ build    ─ pages                               │
   │   ─ qualify    ─ price     ─ verify   ─ distribute                          │
   │                                                                             │
   │   runtime/  step engine · acceptance predicates · checkpoints · leases      │
   │   connectors/  probe + contract test + dormancy state machine        (C3)   │
   └────────┬──────────────────────────────┬─────────────────────────────┬──────┘
            │                              │                             │
   ┌────────▼─────────┐         ┌──────────▼──────────┐        ┌─────────▼────────┐
   │  jarvis-commerce │         │   jarvis-agent      │        │  shared infra     │
   │  offers/orders/  │         │   headless browser  │        │  postgres redis   │
   │  entitlements/   │         │   egress-proxied    │        │  qdrant  ollama   │
   │  fulfilment      │         │   screenshot-proof  │        │  prom  grafana    │
   │  ‼ deployed      │         │   ‼ crash-isolated  │        │  minio (artifacts)│
   │    rarely  (C5)  │         │     from core       │        └───────────────────┘
   └──────────────────┘         └─────────────────────┘
```

### Why exactly four services

Pimlico's monolith (`hermes`, 11.8k lines) was not wrong on day one — it became wrong when a
feature redeploy dropped the scheduler lease, took down the operator surface, and rolled the
payment path all at once. Each split below discharges a specific observed failure:

| Service | Why separate | Deploy cadence |
|---|---|---|
| **jarvis-core** | The factory. Everything that can change weekly. | Often |
| **jarvis-commerce** | **The money path must not be redeployed for feature work.** Pimlico's payment path was repeatedly rolled by unrelated changes and has still never processed a real order. (C5) | Rarely, with a mandatory journey test |
| **jarvis-console** | **When core is down you must still be able to see and act.** Under Pimlico, a hermes roll blinded the operator for ~90s and any real outage blinded them entirely. (C7) | Independently |
| **jarvis-agent** | Headless browser work is the flakiest code in the system and leaks memory. Crash-isolate it, egress-proxy it, and let it die without touching the factory. (C3) | Independently |

Everything else is a **module inside core**, not a service. Resist further splitting: Pimlico's
real coupling problem was n8n, not module count.

---

## 2. The step engine — where C1 lives

Everything in JPD is a **Step**. Not a function call, not a cron job — a declared unit with a
contract. This is the single most important structural difference from Pimlico.

```python
@step(
    id="research.capture_competitors",
    phase="RESEARCH",
    inputs=("need_id",),
    produces=("evidence[]",),
    requires_connectors=("you_com", "databar"),      # dormancy-aware   (C3)
    acceptance=lambda r: (
        len(r.evidence) >= 5
        and all(e.sha256 and e.fetched_at for e in r.evidence)
        and all(e.url_live_at_capture for e in r.evidence)
    ),                                                #                  (C1)
    idempotency_key="need_id",
    timeout_s=300,
    cost_budget_usd=0.50,
    test="tests/stages/test_research_capture.py",     # cannot register without  (C2)
)
async def capture_competitors(ctx) -> StepResult: ...
```

**Non-negotiable rules of the engine:**

1. `StepResult.status ∈ {succeeded, failed, blocked_on_human, skipped_dormant, quarantined}`.
   There is **no `None`, no `unknown`, no default**. The DB column is `NOT NULL` with a CHECK.
   *(Pimlico persisted `status=None` for seven consecutive days.)*
2. A step transitions to `succeeded` **only** if `acceptance(result)` is true. If the predicate
   fails, the status is `failed` and the evidence is retained for diagnosis.
3. A step whose `requires_connectors` are not `live` returns `skipped_dormant` — **never**
   fabricates, never persists partial output. *(Kills the Sintra class outright.)*
4. Every step is **idempotent on `idempotency_key`** and holds a lease. `KILL` on a running step
   is enforced by `WHERE lease_owner = $1` in `advance`/`fail`/`park` — Pimlico's `lease_owner`
   appeared in no WHERE clause, so killing a run resurrected it.
5. Every step declares a `cost_budget_usd`. Exceeding it fails the step rather than the invoice.
6. **A step cannot be registered without a test path that exists.** Startup asserts this. (C2)

### Repair vs retry
Pimlico's repair loop tested `attempts`, which `advance` reset to 0 on every transition — so the
guard was always true and a section the model reliably answered "TBD" looped forever at full LLM
cost. JPD branches on a **durable `repair_count`** with a hard ceiling, and repair is a *distinct
step* with its own acceptance predicate, not a retry of the same one.

---

## 3. The connector contract — where C3 lives

Every external dependency — API, RSS, headless browser, *or human* — implements:

```python
class Connector(Protocol):
    name: str
    kind: Literal["api", "rss", "browser", "human"]

    async def probe(self) -> ProbeResult:          # cheap: is it reachable & authenticated?
    async def contract_test(self) -> TestResult:   # does the response have the SHAPE we parse?
    async def call(self, req) -> Typed             # returns a TYPED artifact, never a string
```

### Dormancy state machine

```
        contract_test pass          2 consecutive probe fails
  live ────────────────────────► live ──────────────────────► degraded
   ▲                                                             │
   │        contract_test pass                 4 total fails     ▼
   └───────────────────────────────  dormant ◄────────────────────
                                        │
                      emits nothing · pipeline routes around it
                      · surfaced in #alerts · never silent
```

**Rules:**
- A connector not in `live` **cannot emit content**. Its steps return `skipped_dormant`.
- `call()` returns a **typed artifact**, never a raw string. Pimlico's entire Sintra incident was
  possible because `_route_sintra_output(output_type, text, ...)` accepted `str`.
- Any output failing validation goes to `dead_letter` with the raw payload — **quarantined, never
  routed**. Nothing in `dead_letter` can reach a publish path; enforced by the publish step's own
  acceptance predicate.
- **Zero-yield is a failure, not a success.** A source returning 0 items for N consecutive runs
  transitions toward dormant. *(Pimlico's `google_trends`, `indie_hackers` and
  `app_store_reviews` have returned 0 every day with `dormant: []`.)*
- Contract tests run **inside the deployed container**, against the real service, on a schedule —
  and their results are the input to the state machine.

### `kind="human"` is a real connector
This is the generalisation of the Sintra bridge and it is what lets JPD use the ~two-thirds of
the owned portfolio that has no API. A human connector's `call()` creates a blocking
`human_task`, posts a card to Telegram, and **suspends the run** until a typed reply arrives.
Same contract, same evidence requirements, same acceptance predicate. See §7.

---

## 4. Data model

Only the load-bearing tables. Every one of them exists because something in Pimlico was missing.

### Discovery
```
sources(id, name, kind, source_type, config, enabled, health_state, zero_yield_streak, …)
signals(id, source_id, external_id, concept, body, url, observed_at, embedding_id, …)
clusters(id, centroid, member_count, first_seen, last_seen)
gate_evaluations(id, run_id, cluster_id, gate, value, threshold, passed, evaluated_at) ← C6
needs(id, cluster_id, title, pain_statement, audience, status, score, promoted_by, …)
```
`source_type ∈ {search, launch, community, review, filing, authority}`
**`authority` is new** — creator/YouTube content. Critical gate rule: all creator channels share
`authority`, so **they can never self-corroborate the cross-source gate.** (Same lesson as
`stackoverflow` + `github_issues` sharing `community`.)

`gate_evaluations` is the fix for C6 — Pimlico's near-miss census lived in per-process memory and
was lost on every restart, making calibration impossible. Persisted, it enables
**counterfactual replay**: *"what would have promoted at severity ≥ 3.5?"* — a SQL query, not a
rebuild.

### Voices — who said it (added 2026-08-07 at operator request)
```
voices(id, kind ∈ {person, company}, display_name, handle, platform, profile_url,
       org_name, org_domain, first_seen, last_seen, enriched_at, contactable,
       contact_ref, do_not_contact)
voice_mentions(id, voice_id, signal_id, need_id, evidence_id, stance, quote,
               observed_at)
```
`stance ∈ {reports_pain, requests_solution, offers_workaround, sells_alternative, endorses}`

**Why this is more than a nicety.** Every signal has an author — the Reddit poster, the GitHub
issue reporter, the App Store reviewer, the company in the 10-K, the creator naming the gap in a
video, the person in the comments saying "this is exactly my problem". Those people **are the
prospects for the solution to that problem.** Capturing them turns discovery from a topic
generator into a **launch audience generator**, and it is nearly free — the author is already in
every payload we parse and then throw away.

Consequences that fall out of one table:
- **Qualification gets real** (A5): "who has this pain and can they pay" stops being an inference
  and becomes a list of named people and organisations.
- **Launch has a warm audience** (F6): the product ships to the exact people who described the
  need, quoting their own words back with a citation.
- **`sells_alternative` is competitive intel** for free — those voices are Phase B competitors.
- **Severity gets a better denominator**: 5 mentions from 5 distinct voices is a market;
  5 mentions from 1 voice is one loud person. Pimlico's frequency gate could not tell these apart.

⚠️ **Hard rules, non-negotiable:**
- `do_not_contact` defaults **true** for anything scraped from a community platform. Promotion to
  contactable requires an explicit lawful basis recorded per voice — a public professional profile
  or an opt-in. Reddit/HN/App Store authors are **evidence, never a mailing list**.
- Outreach to any voice is an **approval-gated** step, never automatic.
- A `person` voice stores the minimum needed to attribute a quote. No profiling, no enrichment of
  private individuals — enrichment applies to `company` voices only.

### Evidence & grounding — the heart of C4
```
evidence(id, url, sha256, fetched_at, http_status, mime, snippet, full_artifact_id,
         source_kind, captured_by_step, live_at_capture)
claims(id, deliverable_id, text, evidence_id NOT NULL, confidence, verified_at)   ← C4
dossiers(id, need_id, kind ∈ {need, research, competitive, pricing}, body, created_at)
```
`claims.evidence_id NOT NULL` is the whole game. **A deliverable with an uncited factual claim
cannot be published — the database refuses it.** Pimlico had no citation field anywhere and sold
27.5k-word products that were pure model recall.

### Solutions & the three tiers
```
solutions(id, need_id, status, …)
artifacts(id, solution_id, tier, kind, sha256, bytes, storage_uri, created_at)
    tier ∈ {roadmap, instructions, deployed}
acceptance_tests(id, solution_id, tier, name, command, expected, last_result, last_run_at)
```
Artifacts are **content-addressed**. Same hashing discipline as evidence and as the source
manifest (C8) — one mechanism, three uses.

### Commerce — exists before any generation code (C5)
```
offers(id, solution_id, tier, currency, price_minor, external_ref, live, created_at)
orders(id, offer_id, buyer_email, buyer_ref, amount_minor, currency, provider,
       provider_ref, signature_valid, status, created_at)
entitlements(id, order_id, buyer_ref, solution_id, tier, granted_at, revoked_at)
fulfilments(id, entitlement_id, status, artifact_id, delivered_at, channel, evidence)
upgrades(id, from_entitlement_id, to_tier, price_delta_minor, order_id)
```
`price_minor` is an **integer in minor units**. Pimlico listed every product at 100× because a
float euro/cent confusion went unnoticed; integers-in-cents plus a read-back guard makes the bug
unrepresentable. `upgrades` is what turns the ladder from three products into one funnel.

### Runtime, humans, integrity
```
runs(id, need_id, phase, status, lease_owner, lease_expires_at, cost_usd, started_at)
steps(id, run_id, step_id, status NOT NULL CHECK(...), attempt, repair_count,
      accepted, evidence_json, cost_usd, started_at, ended_at)                       ← C1
human_tasks(id, run_id, type, title, why, how_md, where_url, verify_command,
            status, assigned_channel, telegram_message_id, expires_at, reply_json)   ← C7
connector_health(connector, state, last_probe_at, last_contract_at, fail_streak,
                 zero_yield_streak, evidence)                                        ← C3
checkpoints(id, run_id, phase, state_json, resumable_from, created_at)               ← §8
source_manifest(path, sha256, image_tag, verified_at, drift_detected)                ← C8
dead_letter(id, connector, payload_raw, reason, created_at)                          ← C3
llm_usage(id, run_id, step_id, model, purpose, tokens_in, tokens_out, cost_usd)
```

---

## 5. Observability — where C2 lives

Pimlico had eleven alert rules, and today **four of its detectors are silently broken**. The
lesson is not "add more rules". It is: **an untested detector is not a detector.**

### Three additions over Pimlico

1. **Synthetic-failure tests.** Every alert rule ships with a paired test that deliberately
   trips it in a sandbox namespace and asserts it fired. Runs weekly.
   `alert_synthetics(alert_name, last_tripped_at, last_result)` — and there is a
   meta-alert: `AlertNeverTripped` fires if any rule has not been synthetically verified in 10 days.
2. **Clamped watermarks.** Every cursor is written as `min(saved, observed_max)`.
   *(`hermes:n8n:last_seen_execution = 1757` against a max real id of 34 has blinded Pimlico's
   n8n watcher since the migration.)*
3. **Service-level gauges only.** A metrics lint fails CI if a gauge feeding an alert is written
   from a request-handling worker. *(`hermes_scan_last_success_timestamp` alternates between two
   values 1.5 days apart across scrapes, because it is per-worker.)*

### Alert thresholds are derived, not guessed
`NoSuccessfulScan` at 10 days for a weekly job cannot fire until two consecutive misses. In JPD
every freshness alert is expressed as **`expected_interval × 1.5`**, read from the job registry —
so a schedule change updates the alert automatically.

### Business metrics — day one, not "later"
`revenue_total`, `orders_total{tier}`, `upgrade_rate`, `cost_per_run`, `margin_per_solution`,
`needs_promoted_total`, `gate_block_reason_total{gate}`, `evidence_rows_per_dossier`,
`uncited_claims_total` *(must be 0)*, `human_tasks_open{age_bucket}`.

Grafana dashboards, in build order: **Revenue** → **Funnel** → **Runs** → **Connectors** →
**Cost**. Deliberately the reverse of Pimlico's instinct — a beautiful pipeline dashboard over a
revenue path that cannot take money is a nicer view of €0.

---

## 6. Discovery sources

### Existing types, carried over
`search` (Google Suggest, Trends) · `launch` (Product Hunt, ~~Indie Hackers~~) · `community`
(HN, ~~Reddit~~, Discourse, StackOverflow, GitHub Issues) · `review` (App Store 1–3★) ·
`filing` (SEC EDGAR 10-K Item 1A)

> ⚠️ **Two are struck through because they cannot be harvested from this host**, and both are
> kept as explicit dormant-with-a-reason connectors rather than deleted: **Indie Hackers**
> removed its RSS feed entirely (§23) and **Reddit** 403s this datacenter IP without OAuth.
> Each remaining type still has at least one live source, so no source *type* is lost.

### New type: `authority`
Creator channels where operators state, out loud, which problems are expensive and unsolved.
This is the highest signal-to-noise source available and Pimlico had nothing like it.

**Seeded channels** (all `source_type="authority"`, all mutually non-corroborating):

| Channel | Why it earns a slot |
|---|---|
| **Alex Hormozi** | Offer construction, pricing, value-equation framing. Directly informs the tier ladder and willingness-to-pay. |
| **Leila Hormozi** | Operational and people-systems pain in scaling businesses — the "expensive unsolved process" seam. |
| **Codie Sanchez** | Boring/overlooked business problems; unsexy, high-willingness-to-pay niches. |
| **Skool** (communities) | Paid-community discussion = pain someone already paid to discuss. Highest intent signal available. **No public API — human/browser connector.** |
| **Liam Ottley** | AI-automation agency demand; explicitly enumerates what clients ask for and cannot get. |
| **Liam Evans** | Automation/agency build patterns and tooling gaps. |
| **Jack Roberts** | Productised-service and delivery-model gaps. |
| *(extensible)* | Registry-driven — add channels without a redeploy. |

**TubeOnAI — the accelerator for this source type** *(added 2026-08-07 at operator request)*

TubeOnAI produces AI summaries of YouTube videos and podcasts for tracked channels, on a
notification cadence. That is exactly the ingestion problem `authority` has: transcript pulling is
slow, quota-limited, and noisy. Where TubeOnAI covers a channel, JPD consumes its summary instead
of transcribing.

*Verified from this box, 2026-08-07:* `api.tubeonai.com` returns **200** at root and a genuine
**404** with a different body hash for a nonsense path — i.e. it discriminates paths and is a real
API host, **not** the supercool-style "identical shell for every path" trap. No published endpoint
documentation was found (`docs.` / `developer.` / `/api-docs` all absent). **The endpoint contract
is therefore UNVERIFIED.**

Handled the way the connector contract requires: `tubeonai` ships **dormant**, with a
`contract_test()` that must pass against a real credential before it can emit a single signal. It
is a *transport optimisation* for `authority`, never a new evidence type — a TubeOnAI-sourced
signal is still `source_type="authority"` and still cannot self-corroborate the cross-source gate.

> ⚠️ **A summary is not a source.** TubeOnAI returns a *paraphrase*. Grounding (C4) requires the
> creator's own words, so every TubeOnAI-derived signal must be resolved back to the underlying
> video and a **verbatim timestamped quote captured** before any claim built on it can be
> published. If that resolution fails, the signal is usable for *discovery* (it can help promote a
> need) but is **barred from `claims`**. This distinction is enforced by a
> `evidence.source_kind='paraphrase'` flag that the publish predicate rejects.

**Extraction pipeline for `authority`:**
```
YouTube Data API v3 (uploads playlist)          ✅ BUILT 2026-08-08 (§22)
  → transcript (captions; fallback: audio → whisper on local ollama host)     ⛔ not built
  → LLM extraction with a strict schema:                                      ⛔ not built
       { problem_statement, audience, evidence_quote, timestamp_s, stated_cost_or_pain,
         is_explicit_ask: bool }
  → evidence row (url = watch URL + &t=<timestamp>, sha256 of transcript segment)  ⛔
  → signal (source_type="authority")            ✅ BUILT — at VIDEO level, not quote level
```
⚠️ **Only the first and last lines exist.** Signals carry the video title, description and
creator; they do **not** carry a verbatim timestamped `evidence_quote`. Until the middle
three exist, an authority signal can promote a need but is **barred from `claims`** — the
identical restriction TubeOnAI carries, for the identical reason. Step 3 needs API budget.
The `evidence_quote` + timestamped URL means every authority-derived claim is **verifiable by a
human in one click**. That is what makes this source type safe to weight highly.

⚠️ **Gate rule (load-bearing):** authority sources are *opinions, amplified*. A need supported
only by `authority` evidence **cannot clear the cross-source gate**. It must be corroborated by
`community`, `review`, `filing`, or `search` intent. This prevents the system from building a
product because one influencer said something compelling.

**Human setup required:** YouTube Data API v3 key (Google Cloud project) — `runbooks/HT-002`
✅ **written 2026-08-08**. Skool has no API — `runbooks/HT-003` (browser connector + human
fallback), still unwritten.

> **Status, 2026-08-08.** Six of the seven seeded channels have a connector implementation
> (§22); **Skool does not** — it is browser-only. All six are **dormant**: the connector code
> exists, the credential does not. The table above lists channels by display name, but the
> connector **refuses to resolve a channel from a display name** — each row needs a `handle`
> or `channel_id` in `sources.config`, which is data, not a redeploy. See `runbooks/HT-002`.

---

## 7. The human bridge — where C7 lives

### Why this is architecture, not a workaround
Two-thirds of the owned tooling has no API. An architecture that can only consume APIs can use a
third of what has been paid for. JPD makes *"instruct a human to drive a UI"* a first-class,
typed, resumable step.

### The `human_task` contract
Every task, without exception, carries:

| Field | Content | Why |
|---|---|---|
| `title` | One line, imperative | Scannable on a phone |
| `why` | What is blocked and what it costs | Pimlico's `⛔ USER ACTION` bullets were skipped for weeks because no consequence was ever stated |
| `how_md` | Numbered steps, exact clicks, exact field names | Prose runbooks decay; steps do not |
| `where_url` | Deep link to the exact page | Removes the "which platform?" tax |
| `verify_command` | A command **you** can run to prove it worked | "Prove it" is not optional |
| `expires_at` | Explicit | An expired approval silently stalled a Pimlico build for 5 days |
| `reply_schema` | Typed expected reply | The output is parsed, not pasted into a blob |

The run **blocks visibly** — `steps.status = blocked_on_human` — and `#human-tasks` shows the
open queue with age. Nothing is ever silently skipped.

### Telegram forum topics
Human tasks route to a **forum supergroup** (Telegram Topics), one topic per stream:

| Topic | Carries |
|---|---|
| `#decisions` | Gate approvals, price approvals, publish approvals |
| `#human-tasks` | The blocking queue — everything with a `verify_command` |
| `#sintra` | **Sintra instruction cards** (see below) |
| `#discoveries` | New needs promoted, with the gate census that let them through |
| `#revenue` | Orders, upgrades, fulfilments |
| `#alerts` | Connector dormancy, synthetic-failure results, drift |

Pimlico's client posts to a single `chat_id` with no `message_thread_id`. JPD's client takes
`(chat_id, message_thread_id)` per stream, from the source registry.
**Setup requires a browser — `runbooks/HT-001-telegram-forum.md`.**

### The Sintra instruction card
Sintra is Cloudflare-blocked from this VPS — verified again today, failing every day since ≈07-25.
Pimlico's response was to keep calling it and publish the error text. **JPD's response is to stop
pretending it is an API.**

`sintra` becomes a `kind="human"` connector. When a step needs Sintra output, it posts to
`#sintra`:

```
🤖 SINTRA TASK  ·  JPD-1042  ·  expires in 24h

WHY   Blocking: solution AP-1042 "Contractor Change-Order Recovery" — sales copy
      variant B. Without it, publish holds at 2 of 3 variants.

WHERE https://sintra.ai  →  bot: Aria  →  new chat

PASTE THIS PROMPT VERBATIM
──────────────────────────────────────────────────
You are writing ad copy for a digital product.
AUDIENCE: small commercial contractors, 5–50 staff.
PAIN (verbatim from research, cite-backed):
  "we lose 6-figures a year on change orders we never billed for"
PROMISE: recover unbilled change orders within 30 days, no new software.
FORMAT: 3 hooks (≤15 words) + 3 body variants (≤60 words) + 1 CTA.
CONSTRAINT: no financial-services claims. No statistics you cannot source.
──────────────────────────────────────────────────

REPLY  Paste Sintra's full output as a reply to THIS message.
       Reply "SKIP <reason>" to release the block and mark the step skipped.

VERIFY jpd human verify JPD-1042
```

Properties that matter:
- The prompt is **generated from real dossier evidence**, not a static template
- The reply is **parsed against `reply_schema`** — a failed parse re-asks, it does not persist
- The result becomes an evidence-backed artifact like any other
- `SKIP` is an explicit, recorded operator decision — not a silent failure
- **Nothing Sintra-shaped can ever auto-publish again**

---

## 8. Recovery checkpoints

Pimlico's checkpoint is a 3,798-line markdown file that a human must read to resume. It is
genuinely excellent institutional memory and genuinely unusable as a resume mechanism.

**JPD splits the two concerns:**

| Artifact | Audience | Form |
|---|---|---|
| `checkpoints` table | The machine | `state_json` + `resumable_from` per run and per phase |
| `CHECKPOINT.md` | The next session (human or agent) | **Generated** from the table + a hand-written "why" section |
| `docs/*.md` | Architecture | Updated incrementally, versioned, diffed in review |

```bash
jpd resume                    # latest checkpoint, all runs
jpd resume --run <id>         # one run
jpd checkpoint write "note"   # explicit, with a reason
jpd verify --last             # re-run the last step's acceptance predicate before continuing
```

**The resume rule** — earned from Pimlico's `[T-1.12]` incident, where a ledger line read
IN-PROGRESS while the work was in fact complete:

> On resume, **re-run the last step's acceptance predicate before assuming anything.**
> A missing DONE line is not evidence of missing work.

Checkpoints are written **on every phase boundary and before every human task**, so the two
places a session actually dies are always covered.

---

## 9. The three-tier offer, mechanically

| | **Roadmap** | **Instructions** | **Deployed** |
|---|---|---|---|
| Produced by | Phase C | Phase D | Phase E |
| Contains | Outcome, milestones, stack selection, effort + cost, risk register | Roadmap **+** full build manual, configs, credentials, acceptance tests | Instructions **+** built, configured, tested, handed over |
| Buyer | Builds it themselves | Has a team/operator | Wants the outcome |
| Acceptance test | Every milestone has an owner, a duration, and a dependency | A competent operator can execute without asking a question | The acceptance tests pass on the delivered system |
| Fulfilment | Artifact download | Artifact download | Artifact + provisioning + handover human task |
| Suggested ratio | **1×** | **3–4×** | **10–15×** |

Pricing is anchored on **observed** willingness-to-pay captured in Phase B (`dossiers.kind='pricing'`),
never on a regex over one page. The *ratio* is a business decision (Charter §8.2).

**The upgrade path is the point.** A Roadmap buyer who returns pays only `price_delta_minor`, and
`upgrades` links the entitlements so fulfilment delivers **only the delta**. This is the highest-
margin revenue in the system and it costs nothing extra to produce.

---

## 10. Testing strategy

Four layers. **A stage cannot be registered without a test path that exists** (§2, rule 6).

| Layer | What it proves | Cadence |
|---|---|---|
| **Contract** | Each connector: real service, real auth, response *shape* we parse | Every 15 min → feeds dormancy state machine |
| **Stage** | Golden input → acceptance predicate holds | Every commit |
| **Journey** | The three buyer journeys + the upgrade, against a sandbox tenant | Every commerce deploy — **blocking** |
| **Integrity** | Source manifest == running image; no drift | Hourly (C8) |
| **Synthetic-failure** | Every alert rule actually fires | Weekly (C2) |

Regression tests are written **in the same commit as the feature**. A PR touching `forge/` with
no test delta fails CI.

---

## 11. Build order

Deliberately inverted from instinct: **prove you can take money before building the thing to sell.**

| Phase | Deliverable | Exit criterion |
|---|---|---|
| **0. Skeleton** | Repo, stack file, postgres+redis, step engine, checkpoints, `jpd` CLI | `jpd resume` works on an empty DB |
| **1. Commerce first** | `offers/orders/entitlements/fulfilments` + checkout + tier-aware delivery | **A real €1 test purchase of each of 3 tiers completes end-to-end.** *This is what Pimlico never did.* |
| **2. Console** | Telegram forum, human tasks, decision cards, Sintra bridge | HT-001 done; a real human task blocks and unblocks a run |
| **3. Connectors** | Registry, probe, contract test, dormancy | A deliberately-broken connector goes dormant within one interval |
| **4. Discovery** | Sources incl. `authority`, funnel, gates, persisted census | One need promoted **autonomously** from ≥2 source types |
| **5. Research** | Evidence capture, competitive, pricing, grounding | A dossier with ≥15 live hash-verified evidence rows |
| **6. Forge** | Roadmap → Instructions → Deployed, acceptance tests per tier | Three artifacts from one need, **zero uncited claims** |
| **7. Market** | Grounded copy, media, pages, gated distribution | A sales page live, sharing correctly, checkout working |
| **8. Close the loop** | Attribution, upgrade funnel, calibration replay | An upgrade completes; a gate is retuned from real census data |

Each phase ends with: a checkpoint, an architecture-doc update, and a green regression suite.

---

## 12. Deployment & operations

Carried from Pimlico unchanged (analysis §A), because it works:

- Docker Swarm, `jarvis_net` overlay, own stack file
- Secrets as Swarm secrets on tmpfs; `CHANGE_ME` ⇒ dormant, never a 401 loop
- Numbered image tags, `:previous` maintained, **deploy with the explicit version tag** —
  never `:latest` (a no-op when the spec already says `:latest`)
- Firewall re-applied **after** every `service create`/port change, then re-probed with the netns
  harness and a known-open control port
- `127.0.0.1` never `localhost` (resolves `::1` first on this host)
- nginx vhost + certbot per hostname; new hostnames get basic-auth **before** the cert is issued
  (a cert publishes the hostname to Certificate Transparency within minutes)

New:
- **`--workers 1` for jarvis-core**, with horizontal replicas instead of in-process workers.
  Removes the entire per-worker-gauge failure class at the root rather than patching each gauge.
- **Source-integrity job** compares the content-addressed manifest to the running image hourly
  and alerts on drift. (C8 — the reversion mechanism is still unidentified on this host.)

---

## 13. Risks, stated plainly

| Risk | Mitigation |
|---|---|
| **The funnel never promotes** — Pimlico's actual outcome | Gates are DB parameters + counterfactual replay (C6). Phase 4 does not exit until one autonomous promotion happens. |
| **Grounding is expensive** | Budget per step (`cost_budget_usd`); you.com `lite` is $0.012/call; $100 free credit. Measure before scaling. |
| **Human tasks pile up and become the bottleneck** | `human_tasks_open{age_bucket}` is a first-class metric with an alert. If the queue grows, the design is wrong — and you will see it. |
| **Deployed tier over-promises** | Its acceptance tests are contractual. If they fail, **the tier is not offered for that solution** — the other two still sell. This is why the ladder exists. |
| **Source files silently revert** (observed, mechanism unknown) | C8 integrity job. Until the cause is found, treat any on-disk file as unproven. |
| **Creator-source over-weighting** | `authority` cannot self-corroborate the cross-source gate. Every authority claim carries a timestamped, one-click-verifiable quote. |
| **Building a second platform while the first is broken** | JPD is parallel and additive. Pimlico's four live defects get fixed in Pimlico, separately and first. |

---

## 14. Open decisions for the operator

1. 🔴 **The products spreadsheet** — not on this host. Tool selection sits behind an adapter
   layer so the swap is cheap, but supply it before Phase 3.
2. **Tier price ratios** — §9 suggests 1× / 3–4× / 10–15×.
3. **Telegram forum group** — `runbooks/HT-001`.
4. **YouTube Data API key** — `runbooks/HT-002`.
5. **Payment provider** — GHL payments are connected and proven on the existing tenant, but that
   tenant is co-tenanted with an unrelated business. Decide: same tenant with an offer namespace,
   or a separate provider (Stripe) for JPD.

---

## 15. BUILD RECORD — phase 0 (skeleton), 2026-08-07

> Written after the fact, from what was actually built and verified. Where the
> implementation departed from the design above, the departure is recorded here
> rather than by quietly editing the design — a design doc that is retro-fitted
> to the code stops being able to tell you the code is wrong.

**Delivered:** `jarvis-core` on Docker Swarm, stack `jarvis`, image `jarvis/core:v3`.
**Exit criterion met:** `jpd resume` runs against an empty database and exits 0.
**Regression suite:** 79 tests, all passing, run **inside the deployed image**.

### Layout
```
/opt/jarvis/
  docs/            00-ANALYSIS 01-CHARTER 02-ARCHITECTURE 03-PIPELINE products-inventory.csv
  runbooks/        HT-001 (telegram) HT-004 (sheet)
  checkpoints/     CHECKPOINT.md  (generated header + preserved hand-written half)
  bin/jpd          host wrapper → docker exec into the running container
  platform/
    docker/        docker-stack.swarm.yml  deploy.sh  .env (0600)
    services/core/ Dockerfile requirements.txt pytest.ini migrations/ src/jarvis/ tests/
```

`src/jarvis/`: `config.py` `db.py` `main.py` `cli.py` ·
`runtime/{types,registry,engine,lease,checkpoints,watermark}.py` · `connectors/base.py`

### Ports — all three verified BLOCKED from a TEST-NET-3 source
| Port | Service | Publish mode |
|---|---|---|
| 5632 | jarvis-postgres | host |
| 6581 | jarvis-redis | host |
| 8900 | jarvis-core | **ingress** |

Added to `/etc/pimlico-firewall.sh` (8900 → `SWARM_PORTS`, 5632/6581 → `BRIDGE_PORTS`).
Rule counts after: `DOCKER-INGRESS` 50, `DOCKER-USER` 95.

### Six deviations from the design above, and why

**1. `core` publishes on INGRESS, not host mode.**
Host mode plus `order: start-first` is unsatisfiable on a single-node swarm — the old
task holds 8900 and the new one is placed with *"no suitable node (host-mode port already
in use on 1 node)"*. Zero-downtime rolls matter more than direct binding, and they will
matter far more for `jarvis-commerce`, where a window with nothing listening is a lost
order. Postgres and redis keep host mode: single replica, stop-first, no roll to protect.

**2. Migration is a one-shot container, not a startup side effect.**
`core` refuses to serve an unmigrated schema — correct, and it means the schema cannot be
migrated *through* `core`. The first deploy attempt deadlocked on exactly this. `deploy.sh`
now runs `jpd db migrate` as a throwaway container on `jarvis_net` after postgres is healthy
and before asserting convergence. It also keeps DDL out of the hands of N racing replicas
during a rolling deploy.

**3. The lease guard checks the RUN's lease, not the step's.**
The design said "`WHERE lease_owner = $1` in advance/fail/park". Implemented literally, that
is **not sufficient** — `steps.lease_owner` is written by the step itself at `_begin()`, so
it always matches and the guard silently passes. `test_killing_a_run_stops_it_writing_a_success`
caught it. The mutation now carries
`AND EXISTS (SELECT 1 FROM runs r WHERE r.id = steps.run_id AND r.lease_owner = $1 AND NOT r.kill_requested)`.
**Generalised rule: assert ownership where it can be taken away from you, not where you
wrote it yourself.**

**4. A successful probe does NOT restore `live`.**
Only a passing contract test does. Reachable is not parseable: a service can be up,
authenticated and returning 200 having renamed the field we depend on, which yields
plausible zeros rather than errors. Recovery is earned, not assumed.

**5. `jpd checkpoint render` refuses to overwrite an unmarked file.**
As designed it would have destroyed a hand-written CHECKPOINT.md on first use — no marker
means `split_why()` returns empty and the generated header lands on top of everything.
Institutional memory has no backup and cannot be regenerated. It now refuses, and prints
the marker line to add.

**6. Tests use a per-test asyncpg pool and refuse a non-test database.**
The suite `TRUNCATE`s. It aborts at import if the target database name does not contain
`test`, with no override flag. Session-scoped pools also produced "attached to a different
loop" errors that read as database faults and are not.

### What phase 0 does NOT include
No pipeline steps are registered (`jpd steps` is empty, by design — the engine ships before
the steps). No connector implementations, only the contract and the state machine. All 23
seeded connectors are `dormant` and cannot emit until a contract test passes. No Telegram,
no commerce logic, no Prometheus scrape config for `jarvis_monitoring`.

### Deploy discipline, as executed
Build → **import check inside the image** (a syntax check is not an import check) → deploy
with an **explicit version tag** → **wait for the new IMAGE ID to be the one running**, not
for "a task is Running" (with `start-first` the old task satisfies a task-count check
instantly and the assertion then fails against a container that was never replaced) →
retry `/ready` → re-apply firewall → re-probe from outside with a control port.

---

## 16. BUILD RECORD — phase 1 (commerce), 2026-08-07

**Delivered:** `jarvis-commerce`, deployed as its own swarm service with its own
version pin. **142 regression tests green**, including 26 journey/adversarial tests.
**Exit criterion: PARTIALLY met** — see "What is and is not proven" below.

### The C5 property, demonstrated rather than asserted
`core` was rolled to `v5` while `commerce` stayed on `v4`, in the same deploy.
Commerce moves only when `docker/COMMERCE_VERSION` is edited, and editing it makes
`deploy.sh` run the journey tests **first, blocking**. Pimlico's payment path was
repeatedly rolled by unrelated feature work and has still never processed an order.

Same image, separate pin — a deliberate trade. A separate image would decouple the
*code* too, at the cost of a shared base image or duplicated runtime. What matters
operationally is that feature work cannot move the money path, and it cannot.

### GHL findings, verified from this host 2026-08-07
| Question | Answer |
|---|---|
| Can the API create a store? | **No** — `/store/store`, `/stores`, `/store/store/list` all 404. HT-005 is browser-only. |
| Is store membership machine-readable? | **Yes** — `excludedStoreIds` on each product. 43 products exclude one store, 9 exclude the other. This is the filterable namespace DEC-002 wanted. |
| Do payments actually work? | **Yes** — a live product page renders a real Stripe checkout (16 `STRIPE` refs, "Add to Cart", "Buy now"). |
| Does `/payments/integrations/provider/whitelabel` prove that? | **No.** It returns `providers: []` because it lists *whitelabel* providers only. An earlier session read this as "payments disconnected" and was wrong. Judge by the rendered checkout. |
| Transport | **httpx required.** urllib's user-agent trips Cloudflare error 1010 and every call 403s. |

### The three checks on the money path
1. **Signature** — enforced from day one. An unconfigured secret **rejects**. Pimlico's verifier deliberately returned VALID when unconfigured so it could be introduced on a live payment path without risk; sound for retro-fitting, wrong as a default.
2. **Amount, from OUR `offers` row** — the payload's amount is recorded, never trusted. Pimlico treated any `amount > 0` as paid, so `amount: 1` would have minted a €297 product.
3. **Idempotency on `provider_ref`** — a UNIQUE constraint, not a check-then-insert race. A replayed webhook finds the existing order and changes nothing.

Every inbound webhook is written to `provider_events` **before** interpretation,
including rejected ones: "we never saw it" and "we rejected it" must stay
distinguishable months later.

### Delivery
- Tokens stored as **sha256, never plaintext** — a download token is a bearer credential.
- **A token is minted only after the artifact file is confirmed on disk.** All three of Pimlico's delivery tokens point at files that do not exist; they were minted from an intention rather than a fact.
- A **sweep** re-checks every artifact behind a live token, because a file can vanish after minting. The buyer must never be the monitoring system.
- Partial delivery is **not** success. `FulfilmentResult.ok` is false if any tier failed, and `undelivered_paid_orders()` makes "who paid and did not receive" a query rather than a grep.
- Each tier is a **superset** of the one below, so Instructions delivers Roadmap too; an upgrade delivers **only the delta**.

### Four defects found by the tests, not by review
1. **Test pollution of seeded tables.** A test that legitimately deletes a `pricing_policy` row left the table with two rows; the next run's snapshot faithfully captured the damage. The suite now **rebuilds the schema from the migrations every session** — a deploy-gating suite must be reproducible from nothing.
2. **`upgrade_quote` reported the symptom, not the cause.** A downgrade fell through to the delta arithmetic and raised "delta must be positive". It now checks direction first and says the ladder only moves upward.
3. **Commerce inherited the image's HEALTHCHECK, which probes :8900** — commerce listens on :8904. The service worked perfectly and was declared unhealthy forever, so swarm killed and restarted it on a loop. **`/ready` alone will not catch this**; `deploy.sh` now also asserts the replica count.
4. **A runbook citing commands that did not exist.** HT-005 referenced `jpd commerce test-ladder` and `jpd commerce orders`. They exist now, are tested against an empty database, and `test-ladder` **refuses while the provider is dormant** and names the outstanding human task.

### What is and is not proven
**Proven end-to-end, against a real database and the real money path:** all three
buyer journeys, the upgrade delta, replay, underpayment, overpayment, bad signature,
unknown product, missing amount, non-live offer, missing artifact, empty artifact,
partial delivery, revoked entitlement, token hashing, download limits, expiry,
revocation, the sweep, and attribution.

**Not yet proven:** a real purchase. That needs HT-005 (the JPD store), and it is the
actual exit criterion. Only the *provider* is stubbed in the journey tests — signature
enforcement, the amount check, idempotency, entitlement, artifact existence, token
minting, redemption and notification are all production code.

### Deliberately not done yet
- **Port 8904 is unpublished.** Nothing needs to reach it until a real offer exists; an unreachable webhook is safer than a reachable one nobody needs. Going live needs an nginx vhost + certbot, the shared secret on both sides, then a re-probe.
- **No notification channel is live**, so `send_delivery` records `skipped_dormant` — an open obligation, visible, never a silent success. Phase 2 wires GHL conversations with Mailgun as fallback (both proven in Pimlico).
- **Still no Prometheus scrape** of either service, so the business metrics exist and nothing reads them. Carried from phase 0.

---

## 17. BUILD RECORD — phase 2 (console), 2026-08-07

**Delivered:** `jarvis-console` as its own swarm service. **202 tests green.**
**Exit criterion: MET** — "a real human task blocks and unblocks a run", proven both
in the suite and live on the deployed stack.

### C7, demonstrated rather than asserted
`jarvis_core` was scaled to **0/0** and, with core completely down:
- `console /ready` and `/tasks` answered normally
- a decision task was **created**
- and **answered**, resolving to `{"choice": "approve"}`

Under Pimlico a hermes roll blinded the operator for ~90s and a real outage blinded them
entirely, because the operator surface lived inside the thing that broke.

**Note the difference from commerce.** Console tracks core's version; what protects it is
being a *separate process*. Commerce is separately *pinned*; what protects the money path
is *not being rolled at all*. Two properties, two mechanisms — do not conflate them.

### The blocking model
`human.request()` is idempotent on `key` and returns `blocked` / `replied` / `skipped`.
A step returns `StepResult.blocked(...)`, the engine records `blocked_on_human`, and the
**run's own status** changes so "waiting on a person" is not mistaken for "stuck".

**Resuming is just running the step again.** The second run finds the resolved task and
proceeds. There is no separate resume path to keep in sync and no callback that can be lost.

### The invariant: a blocked run is visible even when Telegram is down
The task row is written **first**; posting the card is a separate, retryable act. If Telegram
is dormant the task still exists, the run is still visibly blocked, and the failure to announce
is itself recorded. Post-first-record-second would let an outage make work vanish.

`jpd tasks reply <REF> "<answer>"` closes the loop from the CLI. That is not a convenience —
an operator surface with exactly one route in has a single point of failure. It also means
**HT-001 does not block this phase**: the queue works today, HT-001 makes it reachable from
a phone.

### Replies are parsed, not pasted
Three schema kinds — `text`, `choice`, `fields` — plus `SKIP <reason>`. A failed parse
**re-asks and persists nothing**: status stays `open`, `reply_json` stays NULL, and the
operator is told exactly what was wrong. Deliberately not JSON Schema: the full spec brings a
dependency and error messages written for developers, and the operator reading a rejection on
their phone needs a sentence.

`SKIP` bypasses the schema entirely but **requires a reason** — a bare `SKIP` is refused,
because the reason is the whole record of why a step was abandoned.

### The Sintra bridge, and the LinkedIn defect arriving by a new route
Sintra is a `kind="human"` connector. Its card carries the prompt built from real dossier
evidence, and the reply is parsed against `{"type": "text", "min_chars": N}`.

That text schema **rejects pasted error messages** — `[Automation failed`, `Traceback`,
`Page.goto:`, `403 Forbidden`. Worth stating plainly: making Sintra a human connector moved
the failure, it did not remove it. The operator can now paste whatever the UI showed them,
which is the same string Pimlico published to a live LinkedIn account on six consecutive days.
The gate had to exist on this path too.

### Phase-1 gap closed
`notify.send_delivery` now has channels: **GHL conversations → Mailgun → Telegram**, first
live one wins, order from the `notification_channels` table. With none live it still records
`skipped_dormant` — counted as **owed**: money taken, buyer not told, visible.
The Telegram backstop deliberately posts **no download URLs**: a bearer token pasted into a
group chat outlives its purpose.

### Two defects the tests caught
1. **The card and its task row disagreed on the reference.** `human.sintra` pre-renders its
   card (to get the Sintra-specific layout) including `VERIFY jpd tasks show SIN-ABC123`,
   then `tasks.create` generated its own `JPD-F0DC60`. The operator would have been told to
   run a command that finds nothing — and the symptom points at the task store rather than the
   card builder. `create()` now accepts the ref.
2. **Seeded-table pollution, again.** `telegram_streams` is seeded reference data that tests
   legitimately mutate. Same class as `pricing_policy` in phase 1; the fix was to add it to the
   conftest snapshot/restore list. Worth noting the pattern: *any* table seeded by a migration
   and mutated by a test needs restoring, and each new one is found the same way.

### Operator surface
`jpd tasks list|show|reply|expire` · `jpd telegram streams|configure|contract-test|poll` ·
`jpd channels`. The `jpd` wrapper now prefers the **console** container, then core, then
commerce — preferring core would mean the CLI is broken exactly when core is.

### Still deliberately open
- **Nothing scrapes the metrics** — now three services' worth. `jpd_human_tasks_open_by_age`,
  `jpd_human_tasks_unannounced`, `jpd_undelivered_paid_orders`, `jpd_notifications_owed` are
  all live, service-level, and unread. This is the largest remaining gap.
- **No alert rules, therefore no synthetic-failure tests** (C2).
- **No connector is live**, so every channel is dormant by design until phase 3.

---

## 18. BUILD RECORD — phase 3 (connectors + observability), 2026-08-07

**Delivered:** eight real source connectors, the health scheduler, and the whole
observability layer — Prometheus, Alertmanager, 11 alert rules, 10 synthetic tests.
**227 tests green. Exit criterion MET.**

### The exit criterion, proven live
> *"A deliberately-broken connector goes dormant within one interval."*

`product_hunt` was live; its feed URL was repointed at a non-feed; **one** `health.check()`
took it to `dormant` in 0.2s against a 900s interval. It was then not called at all
(`harvest → "not called — connector is dormant"`), and restoring the URL brought it back to
`live` — but only via a **passing contract test**, not a passing probe.

### Eight connectors live, five of six source types — with no credentials
Every endpoint was probed from this VPS *before* the connector was written. The dormancy
machine catches a source that dies later; it is not an excuse to ship connectors that were
never going to work.

| type | connectors |
|---|---|
| community | hacker_news, github_issues, stackoverflow |
| filing | sec_edgar |
| search | google_suggest |
| review | app_store_reviews |
| launch | product_hunt |

The cross-source gate needs ≥2 distinct types, so **the funnel can actually promote**. Only
`authority` needs a key (HT-002), and by design it can never self-corroborate.

**First real harvest: 160 signals, 154 stored, 120 voices captured.**

### 🔴 The SEC User-Agent trap
`sec.gov` returns **503** with an ordinary User-Agent and **200** with a declared
`"Pimlico Services admin@pimlicoservices.com"`. A 503 reads as "their service is down" and
would have sent a future session chasing an outage that does not exist.

### The contract test caught my own mistake
`indie_hackers` **probes 200** — and its contract test fails, because `feed.xml` serves
`content-type: text/html`. I had written the connector after seeing `200` and `316KB` and
assuming RSS. A probe-only health check would have called it healthy for ever, and it would
have sat at zero yield exactly like Pimlico's three "dead" sources.

That is the entire argument for probe and contract being distinct checks, demonstrated
against a real source on the first run.

### Two design defects the tests caught
1. **`degraded` was a one-way trap.** `harvest()` gated on `state != "live"`, so a connector
   that hit three zero-yields stopped being called — its streak could never reach five
   (never dormant) and never reset (never recovered). It would sit in degraded for ever,
   which is precisely the invisible limbo C3 exists to abolish. Only `dormant` skips now.
2. **A synthetic's cleanup left residue that tripped the alert it tested.** `DELETE FROM
   needs` looked like it would cascade the graph away, but the money-path FKs are
   `ON DELETE RESTRICT` on purpose. The cleanup now unwinds in dependency order.

### Observability — the gap carried since phase 0, closed
Prometheus and Alertmanager on `jarvis_monitoring`, **4/4 targets up**, business metrics
being read. Rules are **generated** by `jpd alerts render` — Pimlico's rules exist only as a
docker config with no source file, so `docker cp` returns 0 bytes and the only way to read
them is `docker exec cat`. Rules you cannot diff are rules nobody reviews.

**10 of 11 rules have a synthetic test that fires.** The eleventh, `ServiceDown`, is
structurally unverifiable — proving it means stopping a service in production — so it is
recorded as `never_run`, and the meta-alert `AlertNeverTripped` correctly flags it. The
system reports the truth about its own coverage.

### 🔴 `ConnectorDormant` was a useless alert, and measuring it proved so
The obvious rule — `dormant > 0` — fired permanently: **17 of 24** connectors are dormant by
design, waiting on credentials. An alert that always fires is a thing people learn to close,
which is how Pimlico came to ignore its own monitoring.

Replaced with `ConnectorRegressed`: connectors we have actually *exercised* and which are not
live. Live measurement: **17 → 2**, and both are real (`indie_hackers` serves HTML,
`reddit` 403s). Precision note kept in the code: this is "we tried it and it is not working",
not "it worked once and stopped" — distinguishing those needs a `last_contract_ok_at` column
that does not exist yet.

### Single-owner scheduling
Postgres **session advisory locks**, not a Redis lease. The lock is held by a connection, so
it releases automatically when a process dies — a TTL lease has a window where a dead owner
still holds it and a window where a live owner has silently lost it. Checked before every
tick, not once at startup.

### Known transient, not a defect
For ~5 minutes after a roll, Prometheus retains the old container's series, so alerts can
briefly double-count. The `for:` durations (30m, 1h) mean this never pages. Verified by
waiting for staleness: the duplicate instances expired after ~190s and the steady state was
clean.

### Still open
- **reddit** needs OAuth credentials; **indie_hackers** needs a correct feed URL (or a
  browser connector). Both are visibly dormant with a stated reason rather than silently zero.
- `jpd steps` is still empty — the pipeline steps arrive in phase 4.

---

## 19. BUILD RECORD — phase 4 (discovery), 2026-08-07

**Delivered:** the Phase A funnel as six registered `@step` units — `jpd steps` is no
longer empty. **233 tests green. Exit criterion MET.**

### The exit criterion, on real data
> *"One need promoted **autonomously** from ≥2 source types."*

| need | source types | signals | distinct voices | severity | score | promoted_by |
|---|---|---|---|---|---|---|
| #10 | community + review + **search** (3) | 8 | 5 | 4.50 | 7.56 | `auto` |
| #9 | review + search (2) | 9 | 5 | 4.33 | 7.12 | `auto` |

`gap` is `NULL` on both — deferred to Phase B, never invented. Pimlico's discovery has
promoted **zero** needs autonomously in its entire life.

### 🔴 The deepest finding: corroboration has to be ARRANGED, not hoped for
With each source pointed at a different subject, the funnel produced **zero** cross-source
clusters — measured at every threshold from 0.30 down to 0.08, where clustering already
over-merges into 27-member blobs. The vocabularies simply do not overlap: App Store
complaints about Slack notifications, GitHub CI failures, SEC risk boilerplate and
"best way to reconcile bank accounts" are not about the same thing.

**This very likely explains why Pimlico's funnel never promoted despite 1,690 accumulated
signals.** More volume does not create corroboration between sources that are independently
scattered. Cross-source agreement is not an emergent property of harvesting widely — it is
a consequence of pointing the sources at a shared problem domain. Migration `006` does that,
and it is *config*, not code.

### Three similarity metrics, two of them wrong
| metric | why it failed |
|---|---|
| weighted **Jaccard** | normalises by the UNION, so a 60-word review and a 6-word query scored near zero on 2 shared terms. The seven review documents carrying the only real pain evidence sat outside every cluster, leaving every cross-source cluster at severity 0.0 |
| **cosine** | normalises by the geometric mean — better, still symmetric. Measured: produced *fewer* clusters than Jaccard |
| **overlap coefficient** ✅ | normalises by the SMALLER document. The question is containment: how much of the shorter text is accounted for by the overlap. A search phrase is a compressed need; a review is a verbose one |

### Stemming was not optional
`reconcile`, `reconciliation` and `reconciling` are three different tokens; `invoice` and
`invoices` are two. Two documents obviously about the same problem shared **zero** terms.
It looked like a threshold problem and was a tokenisation problem.

### Four connector defects found by LOOKING at stored signals
1. **`sec_edgar` concepts were `"E X - 9 9 . 1"`** — `' '.join()` over a *string* iterates its
   characters. Every filing concept was unusable.
2. **`app_store_reviews` was pointed at WhatsApp and Starbucks**, labelled "Slack-ish" and
   "Numbers" in a comment I never checked. It produced Spanish-language consumer complaints
   about ads. Every replacement id is now resolved through the iTunes API — my first two
   guesses both returned NOT FOUND, which is precisely how the original mistake was made.
3. **`app_store_reviews` was 80% of the corpus** (188 of 234) because it multiplied its limit
   across seven apps. A connector that floods the window is as damaging as one that returns
   nothing.
4. **`sec_edgar` returned filings from 2001**, every one outside the 30-day window — so
   `filing` contributed nothing while appearing to work.

This diagnosis is only possible because signals are *stored and inspectable*. It is exactly
the investigation Pimlico could never run.

### Two design defects in the gates
- **`distinct_voices` was unsatisfiable for authorless sources.** Google Suggest has no author
  by construction — an autocomplete phrase is a demand signal, not a person — so real search
  signals could never clear `≥ 3`. The gate is about *independence*: an authorless signal is
  independent by construction and counts as one.
- **The severity lexicon was written from imagination** and scored genuine 1-star reviews at
  0.0. It missed "suck", "hopeless", "always breaking", "needs improvement" — how people
  actually complain.

### ⚠️ Honest limits of what was achieved
- **Need #9 is a weaker cluster than its 7.12 score suggests.** It merged "automate accounts
  payable" with App Store reviews about login failures, because `account` is polysemous —
  *accounts payable* and *create account* stem identically. Lexical clustering cannot see the
  difference. Embeddings would; they are blocked on ollama/qdrant credentials (both 401).
  A7's operator gate exists for exactly this, and the auto-promote threshold (7.0) is
  arguably too permissive given it.
- The corpus is **68 admissible signals**. That is small, and the calibration reflects it.

### Config drift, caught by a test
The calibrated `0.42` was applied with an `UPDATE` against **production only**. The test
database, rebuilt from migrations each run, still had `0.18` — so the funnel behaved
differently in tests than in production. Migration `008` writes the calibrated value down,
with its measurement table, so a rebuilt database gets it.

Relatedly: the stage test now sets **its own** threshold. It verifies the promotion
mechanism, not the calibration; leaving them coupled broke it twice for reasons unrelated to
its subject.

### Everything tunable is a row
`sources.config` (queries, seeds, app ids, tags) · `gate_thresholds` · `score_weights` ·
`discovery_params` · `pricing_policy` · `notification_channels` · `telegram_streams`.
Retuning the funnel is an `UPDATE`.

---

## 20. BUILD RECORD — phase 5 (research & grounding), 2026-08-07

**Delivered:** Phase B as five registered `@step` units. **Exit criterion MET.**

| measure | result |
|---|---|
| evidence captured | 50 rows, 33 distinct domains |
| live at capture | 32 |
| **live AND substantive** | **25** (bar: ≥15) |
| unhashed | **0** |
| claims | 28 — 21 gap across 12 domains, 7 pricing |
| **uncited claims** | **0** |
| `needs.gap` | backfilled from `NULL` → 10.0 |
| Deployed tier | **not feasible** — `ghl_payments` and `mailgun` are not live |

That last row is the ladder working: the Deployed tier is withheld for this solution while
Roadmap and Instructions still sell. Degrading gracefully is the reason it is a ladder.

### Credentials: taken from Pimlico, not minted
`ollama`, `qdrant`, `anthropic` and `openrouter` keys were copied from the running Pimlico
stack. Same keys, same accounts — a second key to rotate is a second key to forget. Values
were never printed at any point; verification reported booleans and status codes only.

| service | result |
|---|---|
| ollama | **live** — `nomic-embed-text` present, 768 dims, ~2.4s/call |
| qdrant | **live** — ⚠️ `pimlico_signals` belongs to the other platform; JPD must never write to it |
| anthropic | **live** — claude-opus-5 / sonnet-5 / haiku-4-5 |
| openrouter | reachable, daily limit 15, 9.78 already used |

🔴 **The Anthropic key looked broken and was not.** Three guessed model names
(`claude-3-5-haiku-20241022` and two others) all returned `404 not_found_error`, which reads
exactly like a bad key. `/v1/models` returned 200. **Ask the API which models it serves;
never guess a model name from memory.**

### No you.com key exists anywhere
Checked the Pimlico stack env, the running containers and both `.env` backups. Rather than
block the phase, B1 uses DuckDuckGo lite — verified 200/24KB from this VPS, alongside Bing
and Mojeek as alternates. It sits behind the same connector contract with a real
`contract_test`, so swapping in you.com later is one class and a registry row.

### 🔴 The defect that mattered most: fetched ≠ evidence
The first dossier reported **21 live hash-verified rows** and passed. Reading them showed:

```
#2..#5    4,852 bytes   "Connecting to the iTunes Store."   App Store links don't render
#6..#8   92,056 bytes   "Google Search"                     search result pages
#11       5,753 bytes   "Just a moment..."                  Cloudflare interstitial
```

All genuinely fetched, all genuinely hashed, none evidence of anything. **Counting them is
the same species of lie as Pimlico reporting `processed=4` when all four Sintra prompts had
failed** — technically true, practically false.

`substantive` is now computed at capture time (placeholder detection, SERP detection,
500-char minimum) and the acceptance predicate counts only substantive rows. The immediate
consequence: the next honest run **failed at 8 usable of 15 live**. The bar was right; the
capture had to widen — six queries at ten results each, because measured yield is ~50%.
Narrowing the definition of evidence would have been the easy fix and the wrong one.

### Claims: from corroboration theatre to real distribution
First run: 21 gap claims from **2 domains**, with `worldmetrics.org` producing the same three
gaps twice from two captures of one site — and a perfect `gap = 10.0`. One site repeating
itself is one observation. Now capped at 3 claims per domain with near-duplicate text
suppressed: **21 claims across 12 domains**, and `gap` scores on distinct *domains*, not raw
claim count.

### Re-verification distinguishes dead from changed
`jpd research verify` re-fetches every cited URL. On the first dossier: **23 checked, 21 live,
2 dead, 12 changed**. Those are different failures — *dead* means a broken citation, *changed*
means a live link whose bytes no longer match what was quoted beside it. Collapsing them into
"unverified" would hide the more dangerous one.

### The CLI was degrading the health it reported
`jpd` prefers the **console** container (C7). Console had no credentials, so
`jpd connectors check` probed ollama/qdrant/anthropic, failed, and `record_probe` walked
perfectly healthy connectors toward dormant — the operator tool actively corrupting the state
it exists to report. Console now carries the same credentials. **Any container that can serve
the CLI needs everything the CLI touches.**

### ⚠️ Honest limits
- **`gap = 10.0` is maxed out** (7 contributing domains against a cap of 4). The scale is
  coarse and uncalibrated; it distinguishes "several independent sources found gaps" from
  "one did", and no more than that.
- **Gap claims are LLM extractions.** Each cites the page it came from and the page is hashed,
  so the citation is real — but the *interpretation* is a model's. `claims.supported` exists
  for a verification pass and Phase E's `forge.verify` is where it gets used.
- **Pricing spans €5–€50,000** across 7 domains, median €55. Wide enough that the median is
  weak guidance for the tier anchor; it needs the segment filtering Phase C will apply.
- Content-addressing full raw HTML makes **12 of 23** pages read as "changed" within minutes —
  ads and session ids. Hashing extracted text would be more stable for citation purposes.

---

## 21. BUILD RECORD — phase 6 (the forge), 2026-08-07

**Delivered:** Phases C/D/E as five registered `@step` units. **Exit criterion MET.**

| measure | result |
|---|---|
| artifacts | **3** — roadmap, instructions, deployed |
| **uncited claims** | **0** |
| words | 11,283 across the three tiers |
| files on the shared volume | 3/3, visible from `jarvis_commerce` |
| acceptance tests | 36, covering all three tiers |
| structurally verified | roadmap ✅ |
| factually verified | **blocked — the Anthropic key hit its usage limit mid-session** |

### 🔴 The verification result is honest, and it is not green
`claude-*` now returns `400: "You have reached your specified API usage limits. You will
regain access on 2026-09-01"`. Every fact-check therefore recorded *"verification did not
return a usable answer"*, and the verifier treated each as **NOT supported**.

That is the designed behaviour and it is the important part: **an unverifiable claim is not a
verified claim.** No artifact is marked `offerable`. The system declined to pass work it could
not check, which is precisely the failure mode Pimlico had in reverse — it verified nothing
and shipped everything.

The three artifacts exist, are content-addressed, cite 14 claims with zero uncited, and are
on the volume commerce reads. What is outstanding is the factual pass, which needs API budget.

### Six defects found by running it
1. **`claude-opus-5` returns 200 with a non-text first block.** Extended-thinking models emit
   a thinking block, so `content[0]["text"]` raised `KeyError`, `_llm` swallowed it as `None`,
   and the forge produced **zero sections across three tiers in 691 seconds** having paid for
   every call. Parse by block *type*, never by index.
2. **A long step outlived its own lease.** `forge.generate` ran 580s against a 120s TTL,
   succeeded, and the next step raised `LeaseLost`. **This is the exact failure this codebase
   quotes Pimlico for**, reproduced by me. The engine now heartbeats the lease at TTL/3 while
   a step runs.
3. **Generated work lived only in memory.** 696 seconds of paid output was held in `ctx.data`;
   the next step failed and a re-run hit the idempotency cache (generate had "succeeded") and
   found nothing to package. Sections are now written to `drafts/` the moment they exist, and
   `forge.package` recovers from disk.
4. **`artifacts.solution_id` was NOT NULL** but Phases A–C work at the need level. 696s of
   generation completed and then failed to insert.
5. **`claims.deliverable_id` is single-valued**, so packaging three tiers made each one *steal*
   the citations from the last: roadmap and instructions ended with 0 claims and were marked
   factually OK **vacuously**, while deployed held all 14. Two artifacts were declared
   OFFERABLE because their citations had been taken away, not because they were verified.
   Now a many-to-many `artifact_claims` table — and an artifact citing nothing is
   **unverified**, not verified.
6. **Placeholder detection withheld two good artifacts.** Naive substring matching fired on
   `"custom quote" placeholders` (legitimate prose about vendor pricing) and on
   `"I cannot access the account"` — inside a script the *buyer reads to their bank*. Now
   anchored regexes. A verifier that withholds good work is as damaging as one that passes bad
   work, and it is the failure people "fix" by disabling the gate.

### 🔴 The same lesson, a third time: the CLI writes where it runs
`jpd` prefers the **console** container. Console had no artifacts volume, so `jpd forge run`
wrote **12,759 words of generated product into an ephemeral filesystem** where commerce could
never deliver it. Third occurrence of this class — after `checkpoint render` and
`alerts render`. Console now mounts `jarvis_artifacts`.

**The general rule, stated once more: any container that can serve the CLI needs every mount
and every credential the CLI touches.**

### What the verifier caught when it was working
Before the budget ran out it produced genuinely good rejections — not "the claim is wrong"
but *"the excerpt is only site navigation/menu content"*, *"the source text is almost entirely
CSS/boilerplate"*, *"the excerpt never mentions Tipalti"*. Those point upstream at an
**evidence quality** problem: the `substantive` gate still lets CSS-heavy pages through. That
is the next thing to tighten, and it is a better use of effort than more generation.

---

## 22. BUILD RECORD — the `authority` connector (HT-002), 2026-08-08

**Delivered:** YouTube Data API v3 as six source connectors plus one credential-health
connector. **This closes the `rows_without_code` orphan report for `authority`** — six
`sources` rows that were enabled and unharvestable now have an implementation each.

| measure | result |
|---|---|
| `sources` rows given an implementation | **6** — `yt_alex_hormozi`, `yt_leila_hormozi`, `yt_codie_sanchez`, `yt_liam_ottley`, `yt_liam_evans`, `yt_jack_roberts` |
| credential-health connectors added | 1 — `youtube_data_v3` |
| tests | **262 green** (17 new, run inside the deployed image) |
| quota per full harvest | **12 units** of 10,000/day |
| state | **DORMANT — no key exists on this host.** `contract_test()` has never run against the real API |

### 🔴 This one was NOT probed from the VPS first, and that is a real difference
The other eight sources were each probed from this box before a line was written; the module
docstring lists their status codes. **There is no YouTube key on this host** — not in the JPD
env and not on the Pimlico stack, which is where `ollama`/`qdrant`/`anthropic` came from. So
this connector is written against the *published API contract* rather than *observed
responses*, which is a weaker guarantee, and the tests say so at the top of the file rather
than letting a green suite imply otherwise.

`contract_test()` against a real key is the thing that closes the gap. Until it passes, all
seven stay dormant and emit nothing — which is the state machine working, not a defect.

### Three design decisions, each earned elsewhere in this codebase

**1. Quota is the design constraint, so identity is cached.**
`search.list` costs **100 units**; `channels.list` + `playlistItems.list` cost **1 each**. A
full six-channel harvest is **12 units on the cheap path and 600 on the search path**. Search
resolution is therefore opt-in per source (`allow_search_resolve`) and writes `channel_id` +
`uploads_playlist_id` back into `sources.config`, so it is paid once. An unattended 100-unit
call per harvest exhausts the daily quota in sixteen runs and then presents as a **dead
connector** — and `quotaExceeded` is a 403, the same status as a bad key.

**2. The credential never reaches a detail string.**
YouTube takes the API key as a **query parameter**. `detail` is persisted to
`connector_health` and printed by `jpd connectors`, so any message built from the URL is a
leaked credential in the database and on the terminal. Every failure message names the
*path*. A test asserts the key genuinely is in the outgoing URL and still absent from the
resulting `ProbeResult.detail` — testing the absence alone would pass against a connector
that never sent the key at all.

**3. It refuses to guess which channel it is.**
Identity comes from `sources.config` in cost order — `channel_id` (0 units), `handle`
(1 unit), or `channel` + explicit `allow_search_resolve` (100 units). With none of those it
**raises** rather than searching for a name that looks close.

> A connector that harvests the **wrong** channel is worse than one that is dormant: the wrong
> channel still yields signals, and every gate downstream believes them. This is the same trap
> as the three guessed Anthropic model names that returned 404 and read exactly like a dead
> key (§21). Guessing an identifier is not a shortcut, it is a silent data-integrity failure.

Consequently a wrong `handle` — which returns an empty `items` array, not an error — raises
instead of recording a quiet zero-yield that is indistinguishable from a channel that simply
did not post.

### Why `youtube_data_v3` exists as its own connector
Six sources share one credential. Without a connector that checks the *key* independently of
any channel, a dead key produces six channel-shaped error messages and nothing anywhere says
*the key is the problem*. It probes `i18nLanguages.list` — 1 unit, no channel dependency — so
a failure there is unambiguously about the credential. It has no `sources` row by design and
appears in `code_without_rows`, the same shape as `ollama`/`qdrant`/`anthropic`.

It also surfaces Google's `reason` field, because **`quotaExceeded`, `keyInvalid`,
`accessNotConfigured` and `ipRefererBlocked` are all 403 and need four different fixes.**
Reporting the status code alone sends the next session to the wrong one.

### Playlist tombstones are not signals
YouTube leaves deleted and private uploads in the uploads playlist with the literal titles
`"Deleted video"` and `"Private video"` and an empty description. They parse cleanly and would
be counted as yield. Since **zero-yield is a failure signal** in this architecture (§3),
counting tombstones would inflate a starving source into a healthy-looking one — the exact
condition `HarvestResult` exists to make visible.

### ⚠️ Scope: this is step 1 of §A1b, not all four
Delivered: uploads playlist → video-level signals (title, description, publish time) with the
creator captured as a `voice` (DEC-004).

**Not delivered:** captions → whisper transcription → schema'd LLM extraction of
`evidence_quote` + `timestamp_s`. Step 3 needs the Anthropic budget, which is capped.

Until those exist, an authority signal can **promote a need but cannot back a published
claim** — the same restriction DEC-003 puts on TubeOnAI, and for the same reason: *a video
title is not a verbatim statement of a problem.* The §6 gate rule is unchanged and still
load-bearing — `authority` cannot self-corroborate, so six live channels widen the funnel's
input without being able to promote anything on their own.

### One existing test was changed, deliberately
`test_registry_orphans_are_reported_in_both_directions` asserted that `yt_alex_hormozi` had no
implementation. That was true and is now false. The assertion was re-pointed at `skool` and
`tubeonai` (genuinely still code-less), and the **inverse** assertion was added — so a
regression that unregisters the six channels fails a test rather than silently returning
nothing from six sources.

---

## 23. `indie_hackers` retired — the feed was removed, 2026-08-08

**Delivered:** a connector that reports the *right* reason. `indie_hackers` had been failing
its contract test on `feed is not valid XML: not well-formed (invalid token): line 1,
column 26` — an error that reads like a transient markup change and invites the obvious fix
of finding the new feed URL. **There isn't one.**

| probe | result |
|---|---|
| `/feed.xml` `/feed` `/rss` `/rss.xml` `/atom.xml` `/index.xml` `/posts/feed.xml` `/products/feed.xml`, apex domain | **200, all of them, all HTML** |
| `feeds.feedburner.com/indiehackers` | 404 |
| `<link rel="alternate">` autodiscovery on the homepage | **absent** |
| `/api/posts` `/api/v1/posts` `/api/feed` `/graphql` `/_next/data` | SPA 404 shell |

### 🔴 The finding that matters: a 200 is not evidence when every path returns one
Indie Hackers is a single-page app that serves a shell for **every** path. Proven the way
DEC-003 proved `api.tubeonai.com` was *not* this trap — by hashing bodies:

```
/rss                             sha=f1d0a9999a6d9d27  size=22115
/this-path-is-nonsense-9f3a2b    sha=f1d0a9999a6d9d27  size=22115   ← byte-identical
/feed.xml                        sha=5e3781a87c8bb1a7  size=322214  ← a real HTML page
```

Nine of the ten candidates "worked" by status code. A check built on status codes alone would
have reported nine working feeds and re-pointed the connector at one of them, which would then
have failed exactly the same way — burning the next session too.

**This hash test should be the default for any new HTTP source**, not something remembered
twice a year. `probe()` asking only "did I get a 200" is insufficient against an SPA, which is
precisely why the architecture separates probe from `contract_test()` (§3).

### What changed
`IndieHackers` moves off `RssSource` and into the **known-blocked, kept explicit** section
beside `Reddit`. It no longer advertises a `feed_url` it cannot serve, and `call()` raises with
the verified cause. It is **not deleted** — deleting the class would drop its seeded `sources`
row into `rows_without_code`, which is the report for *"nobody built this yet"*, a different
problem needing a different fix. `probe_url` is deliberately kept: the probe **passes** while
the contract test **fails**, which is the textbook "reachable is not parseable" split and the
reason a passing probe must never grant `live`.

### The source type is not lost
`launch` still has `product_hunt`, which sits on the **same `RssSource` base** and still
returns valid Atom — which is also what rules out a defect in our own parser. A test now
asserts both halves of that, so a future parser regression cannot be misread as another dead
feed.

### Cost of getting this wrong
`observability/alerts.py` already recorded *"indie_hackers serves HTML from its feed URL"* as
one of only **two** genuine regressions surviving the `regressed_count` filter. That note was
right and sat there unactioned. The connector kept failing a contract test every sweep with a
message pointing at XML parsing — which is how a known problem stays open: the record was
accurate, and the error the system emitted contradicted it.

---

## 24. 🔴 The third vacuous pass — a disabled gate was a passed gate, 2026-08-08

Found by deliberately auditing for the shape behind §21's lesson 39 and the
`converge` no-op of §23's follow-up. **It was the worst of the three**, and it
sat in the promotion decision.

```python
@property
def passed(self) -> bool:
    """ALL gates must pass. A gate that can be outvoted is not a gate."""
    return all(r.passed for r in self.results)      # all([]) is True
```

`thresholds()` reads `gate_thresholds WHERE enabled`, and `add()` silently
returned for any gate absent from that dict. Disable the rows and `results` is
empty — so the verdict `passed`, and the docstring's promise was satisfied by
all zero of them.

### Measured, not reasoned about
One cluster — a single authority-only member, no distinct voices, 999 days old:

```
6 gate rows enabled  ->  passed=False  failed=[frequency, severity, distinct_voices,
                                               commercial_intent, recency_days, cross_source]
gate rows disabled   ->  passed=True   failed=[]        ← THE SAME CLUSTER
```

### Why this one was dangerous
**Retuning gates is a DATA operation by design** — stated in the `HttpSource`
docstring as the same rule that governs source config and price ratios. So the
*supported* way to tune a gate was also the way to silently delete it.

And the partial case is worse than the total one. Disable `cross_source` alone
and **rule 1 of this module — `authority` cannot self-corroborate — stops being
enforced**, while `failed_gates` stays `[]` and the log line still reads
`passed=True`. That is the rule that exists to stop the system building a
product because one influencer said something compelling.

Production was never exposed: all 6 of 6 rows have always been enabled. The
defect was latent, which is precisely why nothing caught it.

### The fix — fail loudly, per operator decision 2026-08-08
- `REQUIRED_GATES` names the six. Missing any of them raises `GateConfigError`
  **before a `Verdict` is constructed** — after that point the only remaining
  signal is an empty list that reads exactly like "everything passed".
- `Verdict.passed` returns `False` on an empty result set. Belt and braces: the
  raise is the real guard, but the `all([])` trap itself is now closed.
- The error names the absent gates and says what to do instead ("tune its
  `threshold` value"), and does **not** blame gates that are fine.
- It **raises rather than returning a failing verdict**. Downgrading an operator
  error into "this cluster did not qualify" would hide it behind a plausible
  result — the identical move that let two artifacts be marked verified in §21.
- Threshold *values* remain freely tunable; a test asserts that relaxing all six
  still promotes.

### Why it survived this long
Every existing test asserted that good input passes. **Nothing asserted that a
broken gate CONFIG fails.** `tests/unit/test_gate_config.py` now covers the
negatives — empty verdict, all six disabled, one disabled, the error's contents,
and that a config error is never reported as a merit failure.

### The shape, stated generally — check for a fourth
> **A check with nothing to compare is not a check that passed.**

Three confirmed instances: claims taken away (§21), image IDs identical (§23
follow-up), gate rows disabled (here). Two were caught by the system, one was
caught only by looking. **Audited and cleared** in the same pass:
`forge.verify` (explicitly guards `if not claims: factual_ok = False`),
`HttpSource.contract_test` (fails on `count == 0`), `ghl.configured` and
`research.feasibility` (both `all()` over fixed tuples, never empty).

⚠️ **Still open, deliberately:** three acceptance predicates are tautologies —
`discovery.qualify` (`considered >= 0`), `discovery.promote` (`decided >= 0`),
`research.observe` (`observations >= 0`). They cannot fail. That is intentional
("promoting nothing is a legitimate outcome") but it means those steps have an
acceptance predicate that verifies nothing, which sits badly against the rule
that a verification step which cannot fail is not a verification step.
