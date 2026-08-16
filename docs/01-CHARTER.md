# 01 — JarvisProductDevelopment: Charter

> **Status:** foundational. Version 0.1, 2026-08-07.
> Read `00-ANALYSIS.md` first — it contains the eight constraints (C1–C8) this charter must satisfy.

---

## 1. One-paragraph description

**JarvisProductDevelopment (JPD)** is an automated digital-product factory. It continuously
listens to where real people describe real, expensive, unsolved problems; it verifies that a
problem is frequent, painful, and held by people who can pay; it researches what already exists
and where the gap is, capturing every finding as a citable source; and it then produces a
**solution roadmap**, a **step-by-step implementation manual**, and — where the tooling permits —
a **fully deployed working solution**. Those three artifacts are not by-products of one product;
they *are* three products at three price points, sold from the same run, forming a natural
upgrade ladder. JPD markets and delivers them, and where a required tool has no API, it does not
fail — it issues a precise, typed instruction to a human and waits.

---

## 2. What we intended to build (and are now building deliberately)

The original intent, stated plainly:

> *An automated digital product **discovery**, **creation**, **marketing**, and **delivery**
> platform.*

Four verbs. Pimlico built creation excellently, marketing partially, delivery structurally, and
discovery in a form that has never once fired on its own. JPD builds all four to the same
standard, and adds the two things that were missing entirely: **a closed feedback loop** and **a
commercial ladder**.

### 2.1 The core insight — the pipeline's artifacts *are* the product ladder

This is the central design idea of JPD and everything else follows from it.

A solution to a real problem is discovered in layers. Each layer is independently valuable to a
different buyer:

```
Phase C output  →  THE ROADMAP        "Here is exactly what to build, in what order,
                                       with what tools, at what cost, and why."
                                       Buyer: someone who will build it themselves.

Phase D output  →  THE INSTRUCTIONS   Roadmap + the complete build manual: every step,
                                       every configuration, every credential, plus the
                                       acceptance tests to prove it works.
                                       Buyer: someone with a team, or a capable operator.

Phase E output  →  THE DEPLOYED       Instructions + we built it, configured it, tested it,
                   PRODUCT             and handed over a working system.
                                       Buyer: someone who wants the outcome, not the work.
```

Three consequences, all of them good:

1. **Zero marginal cost for three price points.** The run produces all three regardless.
2. **A natural upsell ladder.** Roadmap buyers are pre-qualified Instructions prospects;
   Instructions buyers who stall are pre-qualified Deployed prospects. The upgrade path is a
   price delta, not a new sale.
3. **Graceful degradation of the *business*, not just the software.** If Phase E fails — the
   tooling isn't there, the build doesn't verify — we still have two sellable, complete,
   honestly-scoped products. Pimlico's all-or-nothing model turned any build failure into zero
   revenue.

### 2.2 What JPD explicitly is not

- Not a content mill. Volume without grounding is what produced €297 of unsourced model recall.
- Not an "AI agent" that improvises. Every step has a declared contract and an acceptance test.
- Not fully autonomous by ideology. Roughly two-thirds of the owned tooling has **no API**.
  Human-in-the-loop is a designed, typed, resumable capability — not an admission of defeat.

---

## 3. Who it serves

| Stakeholder | What they get | How JPD serves them |
|---|---|---|
| **The Buyer** | A specific, evidenced solution to a problem they already feel — at the depth they want to pay for | Three tiers, one checkout, upgrade path preserved |
| **The Operator** (you) | A system that reports its own state truthfully and asks for help precisely | Telegram console: decisions, human tasks, evidence — never raw logs |
| **The Business** | Measurable unit economics per product | `offers/orders/entitlements` from commit #1; cost-per-run from every LLM and API call |
| **The Next Session** (agent or human) | The ability to resume without archaeology | Machine-readable checkpoints, generated docs, `jpd resume` |

---

## 4. The operating principles

Restated from Pimlico's — but each is now bound to a mechanism, because prose principles did not
survive contact with the system.

| Principle | Mechanism that enforces it |
|---|---|
| **A 200 is not proof.** | `StepResult.acceptance_predicate` must evaluate true against captured evidence. No null status is representable. (C1) |
| **Fail loudly, degrade cleanly.** | Connector dormancy state machine; failed output is quarantined to `dead_letter` and can never reach a publish path. (C3) |
| **Nothing unsourced ships.** | `claims.evidence_id` is `NOT NULL`. No citation → publish is refused by the database. (C4) |
| **The detector is also a feature.** | Every alert ships with a synthetic-failure test that trips it on a schedule and asserts it fired. (C2) |
| **Ask precisely, block visibly.** | `human_tasks` are typed, blocking, expiring rows with `what/why/how/where/verify_command`. (C7) |
| **Verify from the box that runs the code.** | Contract tests execute inside the deployed container, against the real service. |
| **The code that runs is the code we wrote.** | Content-addressed source manifest, diffed against the running image on a schedule. (C8) |
| **Never print a credential.** | Status APIs return booleans only. Secrets are Swarm secrets on tmpfs. |
| **Measure what pays.** | Commerce schema precedes generation code. Revenue per run, cost per run, margin per product. (C5) |

---

## 5. Scope of v1

**In scope**
- Discovery across API, RSS, community, filing, and **authority (creator/YouTube)** sources
- Evidence capture and grounding with citations
- Need qualification (who has this pain, can they pay)
- Roadmap / Instructions / Deployed generation, with acceptance tests per tier
- Three-tier offers, checkout, entitlement, tier-aware fulfilment, upgrade path
- Marketing: grounded sales copy, media, sales pages, distribution behind approval gates
- Human-task bridge (Telegram forum topics), incl. the **Sintra instruction thread**
- Regression suite (contract / stage / journey / integrity) gating every deploy
- Machine-readable recovery checkpoints

**Out of scope for v1** (deliberately, with reasons)
- Paid advertising spend — no attribution loop yet; would spend blind
- Multi-tenant / white-label — no customer demands it; adds a hard security boundary
- Migrating Pimlico's 9 live products — JPD runs **in parallel**; Pimlico keeps selling
- Replacing the existing GHL tenant — it is co-tenanted with an unrelated business; JPD uses
  its own offer namespace and filters accordingly

---

## 6. Relationship to Pimlico

JPD is **parallel and additive**, not a migration.

- Pimlico keeps running, keeps its 9 live products, keeps its sales pages and checkout.
- JPD gets its own tree (`/opt/jarvis`), its own database, its own Swarm stack, its own
  hostnames, its own Telegram forum.
- **Shared, read-only:** the host, the firewall harness, ollama, qdrant *(separate collections)*.
- **Never shared:** databases, Redis keyspaces, secrets, image namespaces, alert rules.
- The four critical Pimlico defects found on 2026-08-07 are fixed **in Pimlico**, separately.
  JPD does not inherit them, but it also does not excuse leaving them live.

---

## 7. Definition of done for v1

JPD v1 is complete when **all** of the following are true, each proven by an automated test —
not by inspection:

1. One need is discovered **autonomously** (no human override), from ≥2 distinct source types,
   with a persisted gate census showing why it cleared.
2. Its Research Dossier contains ≥15 evidence rows with live, hash-verified sources.
3. A Roadmap, an Instructions manual, and a Deployed artifact are produced from that one need,
   each passing its own acceptance tests, with **zero uncited factual claims**.
4. All three tiers are purchasable, and one **real purchase of each tier** completes
   `order → entitlement → fulfilment → delivery → notification`, verified by the buyer
   receiving the correct artifact for the correct tier.
5. One **upgrade** (roadmap → instructions) completes and delivers only the delta.
6. Every connector has a passing contract test, and a deliberately-broken connector is
   automatically marked dormant within one probe interval.
7. Every alert rule has been tripped by its synthetic-failure test in the last 7 days.
8. A `jpd resume` from the latest checkpoint reconstructs full state on a cold start.
9. The source-integrity manifest matches the running images.
10. Cost per run and revenue per run are both queryable in a single SQL statement.

> Note the shape of that list: **six of ten criteria are about the system's ability to tell the
> truth about itself.** That is deliberate, and it is the difference between this and Pimlico.

---

## 8. Open input needed from the operator

1. 🔴 **The products spreadsheet.** Not present on this host. `00-ANALYSIS.md §E` reconstructs
   the inventory from the AppSumo triage and the live credential registry, but tool selection
   should be driven by your actual list. The adapter layer makes the swap cheap — supply it and
   the registry is regenerated.
2. **Pricing for the three tiers.** Current Pimlico prices (€147–€497) were set by a regex over
   one Gumroad page. JPD prices from observed willingness-to-pay evidence, but the **ratio**
   between tiers is a business decision. Suggested starting point in `02-ARCHITECTURE.md §9`.
3. **The Telegram forum group.** Requires a browser; runbook is
   `runbooks/HT-001-telegram-forum.md`.
4. **Which creator channels beyond the seven named.** Alex Hormozi, Leila Hormozi,
   Codie Sanchez, Skool, Liam Ottley, Liam Evans, Jack Roberts are seeded. The source registry
   accepts additions without a redeploy.
