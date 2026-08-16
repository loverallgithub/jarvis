# 00 — Analysis of Pimlico, as design input for JarvisProductDevelopment

> Purpose: this is **not** a bug list. It is the extraction of *design constraints* from a
> platform that was built correctly and still failed to earn a euro. Every principle in
> `02-ARCHITECTURE.md` traces back to a numbered finding here.
>
> Evidence base: full live verification of Pimlico on **2026-08-07** (netns exposure probe,
> Prometheus scrape sampling, Redis/Postgres inspection, n8n API, GHL API, container-vs-source
> diffing), plus `/opt/ops/checkpoints/CHECKPOINT.md` (3,798 lines, sessions 1–17).

---

## A. What Pimlico got right — carry these forward unchanged

These are not accidents and rebuilding them differently would be a regression.

| # | Thing | Why it works | Carry into JPD |
|---|---|---|---|
| A1 | **Swarm + overlay + explicit firewall reconciliation** | Verified today: every Docker-published port returns `000` from a TEST-NET-3 source with a valid control port. `ufw` does not cover Docker; a dedicated idempotent script does. | Yes — same model, same netns probe harness |
| A2 | **Credentials as Swarm secrets, `CHANGE_ME` treated as *absent*** | An unfilled integration stays **dormant** rather than 401-looping. `credential_status()` returns booleans only — the diagnostic can never leak a secret. | Yes — extend with a health state machine (see D3) |
| A3 | **Single-owner scheduler via Redis lease** | `hermes_scheduler_service_up = 1.0` on 10/10 scrapes today. Fixed real double-spend. | Yes |
| A4 | **Per-stage checkpointing so a crash resumes, not restarts** | `pipeline_checkpoints` has 11 stages × real rows; a 28k-word build survives a redeploy. | Yes — but make checkpoints machine-readable (D5) |
| A5 | **Numbered image versioning with `:previous` and archive/restore** | Round-trip proven. Rollback is one command. | Yes |
| A6 | **Telegram as the operator surface** | 41 commands, chat-gated, `secret_token` enforced. Works. | Yes — but as its **own service** (D6) |
| A7 | **Self-hosted delivery and sales pages** | Correct call: no LMS in the portfolio exposes create-course → enrol → deliver. Verified again today — all pages 200, checkout URLs product-specific and correct. | Yes |
| A8 | **Stated engineering principles** — "a 200 is not proof", "fail loudly, degrade cleanly", "verify from the box that runs the code", "never print a credential" | Correct principles. | Yes — but **enforce them in types, not prose** (D1) |

**The infrastructure tier was never the problem.** JPD reuses it almost verbatim.

---

## B. The seven failure classes — each becomes a structural constraint

### B1. Steps record that they *ran*, not that they *worked*
Verified live today:
- `production_run.recorded product=seo_loop stage=content_published **status=None**` — every day for 7 days. Nothing inspects the status. The daily SEO loop may be publishing nothing; OpenRouter's own `usage_weekly = $0.045` says it almost certainly is.
- `_route_sintra_output` persists whatever string it receives, with **no success check**.
- The morning brief prints "🤖 Sintra: daily prompts processed" whenever a Redis key exists — including today, when all four prompts failed.

> **Constraint C1 — Evidence-carrying results.** A step returns a typed `StepResult` whose
> `status` is an enum with no null member, carrying `evidence[]`. A step cannot transition to
> `succeeded` unless its own declared **acceptance predicate** evaluates true against that
> evidence. The persistence layer rejects a null status at the schema level.

### B2. Failure detection itself silently died
Four independent detection failures, all live today, all the same shape:
- **The n8n failure watcher is permanently blind.** `hermes:n8n:last_seen_execution = 1757`; the local n8n's highest execution id is **34**. `id > last_seen` is empty forever. WF05 errored 2026-08-01T08:30Z and nothing reported it. The cursor is a leftover from the pre-migration remote instance.
- **`hermes_scan_last_success_timestamp` is per-worker.** Ten consecutive scrapes alternate between the correct value (08-02) and a stale one (07-31). The alert evaluates a series that jumps 1.5 days backwards at random.
- **Three of fourteen discovery sources return 0 items every day** (`google_trends`, `indie_hackers`, `app_store_reviews`) and `dormant: []` never flags them.
- **`NoSuccessfulScan` uses a 10-day threshold for a weekly job** — it cannot fire until two consecutive misses.

The roadmap's own conclusion was right: *"The single highest-leverage item is #6 [make failure detectable]. Every other failure persisted because nothing reported it."* The alerts were built — and then the detectors rotted, because **nothing tests the detectors.**

> **Constraint C2 — Detectors are tested like features.** Every alert rule ships with a
> **synthetic-failure test** that deliberately trips it on a schedule and asserts it fired.
> Every cursor/watermark is clamped (`min(saved, observed_max)`) so an instance swap cannot
> wedge it. Every gauge that feeds an alert is **service-level**, never per-worker — enforced
> by a metrics lint in CI.

### B3. A dead external dependency became published content
Sintra's Playwright login has timed out every day since ≈07-25 (Cloudflare from this VPS). Because nothing checked, the literal string
`"[Automation failed: Page.goto: Timeout 30000ms exceeded...]"` was stored as market intelligence, stored as a video script, queued as LinkedIn copy — **and posted**. Hermes logs confirm `content360_daily.done posted=1 remaining=0` on 08-02, 03, 04, 05, 06 and 08-07.

This is almost certainly the "recurring empty-content published posts" the previous session flagged as an unexplained third-party account mystery. It is not third-party. It is brand-visible, and it is running right now.

> **Constraint C3 — The connector contract.** Every external dependency declares
> `probe()` (cheap liveness), `contract_test()` (proves auth + response *shape*), and a
> **dormancy state machine** (`live → degraded → dormant`). A connector not in `live` cannot
> emit content. Output that fails validation is quarantined to a `dead_letter` table and can
> never reach a publish path. **Content is a typed artifact, not a string.**

### B4. Products are unsourced model recall
`stage_research` performs **no research** — a DB read and a title split, zero LLM and zero network calls. `stage_verify` proves *completeness* (section counts, `lorem ipsum` regex), never *truth*. There is **no citation field anywhere** in the schema. `gap` carries the second-highest score weight (0.25) with no competitive data at all, and `appraise` sets a €297 price from a regex over one Gumroad page.

> **Constraint C4 — Evidence-first, grounded by construction.** Nothing enters the system
> without a captured, hashed, stored `evidence` row (url, fetched_at, sha256, snippet). Every
> factual claim in a deliverable carries a `claim → evidence_id` foreign key.
> **No citation → cannot publish.** This is a schema constraint, not a review step.

### B5. There is no revenue schema, so the only thing that matters is unmeasurable
Verified today: `webhook:events:recent` holds exactly **one** event, ever — a 07-31 test with `amount: 0`, `product_id: "unknown"`, `signature_valid: false`. **No `kind='fulfilment'` run has ever existed.** No `orders` table. No `revenue` table. The `products` table has five columns — `id, name, description, active, created_at` — **no price**. GHL is the sole source of truth, and its location holds 53 products of which only ~10 are Pimlico's; the rest belong to an unrelated hiking/tours business.

> **Constraint C5 — Commerce is a first-class, independently-deployed domain.**
> `offers / orders / entitlements / fulfilments` exist from commit #1, before any generation
> code. The money path is its own service, deployed rarely, and never redeployed for feature work.

### B6. The autonomous loop has never fired autonomously
Every `product_opportunities` row dates from 2026-07-18 except one from 07-31 — and that one (score **3.2**) was a *manual human override*, far below the 8.0 auto-build threshold. Three scans since promoted **0**. On 08-01 exactly one cluster reached the cross-source gate for the first time ever.

The funnel is not broken; it is **uncalibrated**, and there is no way to calibrate it because `FUNNEL`'s near-miss census lives in per-process memory and is lost on restart. Three weeks and ~1,880 accumulated signals produced zero autonomous identifications.

> **Constraint C6 — Calibration is a feature, not an afterthought.** The gate census is
> persisted per run (`gate_evaluations`), charted, and gates are **parameters in the database**
> with a recorded change history — tunable without a redeploy, and always answerable:
> *"what would have promoted if this gate were 4.5 instead of 5?"* (counterfactual replay).

### B7. Human-in-the-loop steps live in prose and are executed from memory
The pattern repeats across every session: `⛔ USER ACTION`, `BLOCKED — needs a browser`, `DECISION-NEEDED`. These are markdown bullets in a 3,798-line file. Several were silently skipped for weeks (`GETLEAD_WEBHOOK_SECRET` was in `.env` but never `--env-add`'d, so the Getlead handoff skipped on **every** publish since it was built).

Related: **the source tree silently reverts.** Verified again today — `browser-agent/src/main.py` on disk (mtime Jul 23) still carries a docstring that was explicitly corrected on 07-31; the *running container* has the fix. A rebuild from disk ships the stale file.

> **Constraint C7 — Human tasks are typed, blocking, expiring database rows** with
> `what / why / how / where / verify_command / expires_at`, rendered as a Telegram card, and a
> run **cannot advance past** an open blocking task. And:
> **Constraint C8 — Source integrity is continuously verified** by a content-addressed manifest
> diffed against the running image, on a schedule, alerting on drift.

---

## C. What was *not* wrong, and must not be "fixed"

Recording these so a future session does not waste a day rediscovering them:

- **GHL cannot create funnels or landing pages via API** (read-only + redirects only; snapshots cannot be pushed). Self-hosting the sales page is correct.
- **GHL's product `medias` array accepts only image/video** — a PDF cannot be attached. Not a bug to fix.
- **GHL price API takes euros, not cents.** A read-back guard exists; keep it.
- **The GHL product LIST response omits `medias`** — only a single GET is authoritative. Do not conclude media is missing from a list call.
- **Instantly's API is gated to Hypergrowth ($97/mo).** A Growth-tier key cannot work.
- **ManyChat cannot cold-message** — 24-hour window is a Meta platform rule.
- **WebinarKit cannot create webinars or upload video via API** — UI only. Registration *is* automatable.
- **No LMS in the portfolio exposes create-course → enrol → deliver.** Self-hosted delivery stays.
- **This VPS's datacenter IP is hard-blocked** by TrendHunter, Upwork, Indeed, Reddit `.json`, Sintra (Cloudflare). Verify from *this box*, never from a laptop.
- **Headless Chromium cannot decode H.264** — a video playback check there is a guaranteed false negative. Use `ffprobe`.
- **`localhost` resolves to `::1` first on this host.** Always `127.0.0.1`.
- **`docker service update --force` does not re-read `env_file`**; `--image <repo>:latest` rolls nothing when the spec already says `:latest`.
- **`docker exec` without `-i` silently ignores heredoc stdin and exits 0.**

---

## D. The eight constraints, consolidated

These are the acceptance criteria for the JPD architecture. An architecture that does not satisfy all eight is rejected.

| ID | Constraint | Kills failure class |
|---|---|---|
| **C1** | Evidence-carrying results; no null status; acceptance predicate per step | B1 |
| **C2** | Detectors are tested; watermarks clamped; alert-feeding gauges are service-level | B2 |
| **C3** | Connector contract: probe + contract test + dormancy state machine; content is typed; failures quarantined | B3 |
| **C4** | Evidence-first: hashed sources, `claim → evidence_id`, no citation → no publish | B4 |
| **C5** | Commerce is first-class and independently deployed | B5 |
| **C6** | Gates are DB parameters with persisted census and counterfactual replay | B6 |
| **C7** | Human tasks are typed, blocking, expiring rows with runbooks and verification | B7 |
| **C8** | Source integrity continuously verified against the running image | B7 |

---

## E. Inventory reality check — what we can actually build with

⚠️ **The "available products spreadsheet" is not on this host.** I searched the entire filesystem.
What follows is reconstructed from the AppSumo triage (CHECKPOINT §4.13, 45 tools, every docs URL
re-fetched) and the live credential registry. **Supply the spreadsheet and this table gets
replaced** — tool selection in `02-ARCHITECTURE.md` is deliberately behind an adapter layer so
that swap is cheap.

### Verified live today — credentials actually present (6 of 34 registry entries)
`content360` · `getlead` · `instantly` · `success_ai` · `supercool` · `thoughtly`

### Platform-level credentials in `.env` (verified working in prior sessions)
GoHighLevel (Private Integrations key) · OpenRouter (`limit: 15`/day cap, `usage_weekly $0.045`) ·
ElevenLabs (Creator, 300k chars/mo) · Mailgun (`mg.pimlicoservices.com`, sending restored) ·
WebinarKit · n8n (local) · Anthropic · Telegram

### Owned but **not configured** (27 registry entries — credentials never set)
`ahrefs, alttext, brandnav, crunchbase, databar, dmarc_report, exploding_topics, fliki, foxly,`
`glimpse, google_ads, hippovideo, kwhero, manychat, odin, periodix, producthunt, pxl, rankatom,`
`reddit, reoon, semrush, sendfox, sparktoro, subscribr, wope, you_com`

Of these, the ones with **real, documented, re-verified APIs** worth wiring first:
**Databar** (160+ data providers, REST + MCP — best research fit) · **you.com Research**
($0.012/lite call, $100 free credit) · **AltText.ai** · **Odin AI** · **Foxly / pxl.to**
(trackable links = attribution) · **DMARC Report** · **Hippo Video**

### Dead — do not design against
Spiritme · Augmental Learning · BlogHunch · RankAtom · TopicMojo · Sintra *(via API/browser from this IP)*

### Manual-only (UI, no API) — these become **Human Task cards**, not integrations
SuperCopy.ai · Taja · Maekersuite · StoryBlaze · Katalist · Magic Bookifier · Jupitrr ·
Nytro SEO · WebSite Auditor · SpiderNow · TruConversion · Intellifluence · Adsbot · KingSumo ·
QR Diffusion · Vzy · Steppit · Wope · KWHero · Biteplay · **Skool** · **Sintra**

> The single most valuable design consequence of this inventory: **roughly two-thirds of the
> owned tooling has no API.** An architecture that can only consume APIs can use a third of what
> has been paid for. JPD therefore treats *"instruct a human to drive a UI"* as a **first-class,
> typed, resumable pipeline step** — not as a failure mode. That is what the Sintra→Telegram
> bridge is, generalised.

---

## F. Verdict

Pimlico is a **well-built chassis with a correct engine and no closed loop**. The build tier is
genuinely real — 9 live products, 24–28k words each, real PDFs, generated imagery, showcase
videos, live sales pages with working checkout. That work holds up and much of it is portable.

What never closed:
- **the intake end** — zero autonomous identifications in three weeks
- **the exit end** — zero orders, ever, and nowhere to record one
- **the feedback loop** — four live detection failures, so the system cannot tell you it is failing

JarvisProductDevelopment is not a rewrite for its own sake. It is the same domain, rebuilt so
that **the loop is closed at both ends and the system is structurally incapable of lying about
its own state.**
