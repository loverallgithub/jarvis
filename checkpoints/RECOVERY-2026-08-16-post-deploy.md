# Recovery checkpoint — 2026-08-16 post-deploy (operator: admin@pimlicoservices.com)

Supersedes `RECOVERY-2026-08-16-claude-session.md` (kept for the audit trail).
**Resume rule:** re-run the last step's acceptance predicate before assuming
anything — this file's predecessor said "scheduler: design pending" while the
scheduler was fully built. Verify, then trust.

## What is running (verified live 2026-08-16 ~07:30 UTC)

- Stack `jarvis`: core + console on **jarvis/core:v78** (image `2b367e11e06d`,
  verified by ID), commerce pinned **v4**. Migrations 001–018 applied.
  `jpd doctor`: all checks passed. Suite green inside the deployed image
  (~710 tests, 4 skips).
- **Scheduler is LIVE**: `jarvis-scheduler.timer` enabled, 15-min ticks,
  host side is flock + `jpd scheduler tick` only. First tick (07:15–07:27)
  ran artifact_sweep, notification_retry (first-ever stamp), discovery.funnel
  (run 25, no duplicate needs — dedup live), and forge.build on need 14;
  research.dossier correctly deferred ("spend slot used").
- `console.expire_tasks` stamps on empty sweeps (the expire_due fix, live).
- `integrity.manifest_check` disabled by migration 018 (no driver ever
  existed); `commerce.notification_retry` has a real driver
  (`notify.retry_owed`: same-row retries, re-minted tokens, 5-attempt cap,
  refuses when no channel is live).
- Alert synthetics: **11/11 fire**, ServiceDown included.
- Offer-description carve-out shipped (operator decision):
  `is_offer_description()` in forge/verify.py — audience-targeting sentences,
  and price sentences ONLY when every amount matches `offers.price_minor`.
  New free command: `jpd market remeasure <need>`.

## Pipeline state

- **Need 13: sales page PUBLISHABLE** — 3 tiers, 3 live offers, 100% cited,
  zero placeholders (`need-13-5b9ca2e4b520.html`). Reached by remeasure
  (free) + surgical recopy of 5 blocks that carried author-note placeholders
  (~$1.25 via OpenRouter fallback).
- **Need 14: three artifacts built (69/70/71), structural OK, NOT offerable**
  — factual pass 8/14 supported; the 6 unsupported are verify calls refused
  under the Anthropic cap (designed behaviour). Fix: lift cap → 
  `jpd forge reverify 14` (14 LLM calls, no regeneration).
- Need 13 artifacts may have the same pending reverify from Aug 8 —
  check `jpd forge reverify 13` after the cap lifts.
- Needs 9–12: duplicates of 13/14, **kept promoted by operator decision**
  (2026-08-16) — the scheduler WILL spend research budget on them
  (~$1.50 ceiling each) on upcoming ticks. Park them if that changes:
  `UPDATE needs SET status='parked' WHERE id IN (9,10,11,12);`
- market.launch 13: REFUSED as designed — 5/5 voices community-scraped, no
  lawful basis. Outreach waits on a human recording lawful basis per voice.

## Blocked on the operator (see /opt/jarvis/NEXT-STEPS.txt for full steps)

1. **CAP-001** — lift the self-imposed Anthropic spend cap
   (console.anthropic.com/settings/limits + per-key limits). Then
   `jpd forge reverify 14` and `jpd forge reverify 13`.
2. **HT-002** — YouTube Data API key + six channel handles (free, ~10 min,
   unblocks 7 connectors / the whole authority source type).
3. **Reddit OAuth** — create a script-type app; once
   JPD_REDDIT_CLIENT_ID/SECRET exist, the connector needs OAuth CODE
   (currently a stub that raises) — a session task, ~30 min.
4. **Mailgun** — only needed to actually email buyers; nothing blocks today.

## Money

- OpenRouter (current LLM route while capped): ~$21.48 credits left,
  $15/day key limit. Whole build month cost ~$7.17.
- Anthropic: monthly cap resets 2026-09-01 00:00 UTC if not lifted sooner.
- llm_usage table is EMPTY — cost tracking never populated (known gap).

## Git

Repo at /opt/jarvis (created 2026-08-16 for /ultrareview).
`main` = `09eba02` (pre-session snapshot). This session's work:
`session/2026-08-16-scheduler-and-carveout` = `11ce60d` (currently checked
out). Secrets are gitignored (.env*, settings.local.json). Ultrareview of the
branch was suggested to the operator; launch state unknown at write time —
check with the operator, not this file.
