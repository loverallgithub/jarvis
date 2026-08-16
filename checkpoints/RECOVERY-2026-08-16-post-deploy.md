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

## UPDATE 2026-08-16 ~10:17 UTC — NEXT-STEPS task 2 (YouTube) COMPLETE
- JPD_YOUTUBE_API_KEY installed by operator; `jpd connectors check youtube_data_v3` → live.
- All six yt_* handles set via UPDATE on sources.config:
  @AlexHormozi, @leilahormozi, @CodieSanchezCT, @LiamOttley, @liamevansyt, @Itssssss_Jack
- All six harvested successfully (uploads playlists cached): 144 admissible signals stored
  (alex 25, leila 24, codie 20, ottley 25, evans 25, jack 25).
- `jpd connectors` now 20/33 live; no yt_* rows in orphans output.
- Operator also supplied @nicksaraev and @MyFirstMillionPod — NO source rows exist for
  these; not added (would need a decision to expand the authority roster).
- Acceptance predicate to re-verify: `jpd connectors check youtube_data_v3` and
  `jpd connectors harvest yt_alex_hormozi` (>0 signals).

## UPDATE 2026-08-16 ~10:27 UTC — authority roster expanded to 8, v79 DEPLOYED
- Operator decision: add Nick Saraev and My First Million as authority sources.
- Commit 5ef1422 on session/2026-08-16-scheduler-and-carveout: YtNickSaraev +
  YtMyFirstMillion subclasses, migration 019 (seeds both rows WITH handles),
  wiring test extended to eight. Full unit suite green in-image (126 tests).
- Deployed core=v79 (commerce stays v4, journey tests not triggered).
  Migration 019 applied. All services converged, /ready green, 1/1.
- Both new connectors live and harvested: yt_nick_saraev 24 admissible,
  yt_my_first_million 25 admissible. Roster now 22/35 live, no yt_* orphans.
- FIXED: platform/docker/.env line 23 was malformed (YouTube key line had a
  pasted ` jarvis_core` fragment after the quoted value) — `source .env`
  failed, which would have aborted any deploy. Repaired in place, key intact.

## UPDATE 2026-08-16 ~10:30 UTC — forge reverify 13 & 14 run via OpenRouter fallback
- Anthropic cap still in place (resets 2026-09-01); every verify call failed over
  to OpenRouter automatically (anthropic/claude-haiku-4.5). No config change.
- Need 13: 14/14 claims supported — 3/3 tiers OFFERABLE (roadmap, instructions,
  deployed). Sales page remains publishable.
- Need 14: 7/14 supported — 0/3 offerable, all tiers withheld. The 7 failures are
  all 'gap' (negative) claims and the verifier is WORKING, not broken:
  claims 5 and 7 are outright contradicted by their own sources; the other five
  are "absence of X cannot be verified from this excerpt". Upstream claim-shape /
  evidence problem — needs forge repair or regeneration, not another reverify.

## UPDATE 2026-08-16 ~10:4x UTC — v80 DEPLOYED, authority roster now 28, all verified
- Commit 53389f6: batch-2 expansion (20 operator-selected channels), migration 020.
- Deploy verified: core + console on jarvis/core:v80, migration 020 applied,
  28 yt_* rows in sources.
- ALL 20 new connectors live and harvested with >0 signals; every handle
  resolved to a real channel; all 28 rows now have uploads_playlist_id cached
  (1-unit path from here on). Lowest admissible: yt_simon_squibb 22/25 — just
  Deleted/Private-video filtering, not a fault.
- Roster: 42/55 live, 0 yt_* orphans. Authority sweep total ~688 admissible
  signals across 28 channels.

## UPDATE 2026-08-16 ~11:0x UTC — scan-source expansion built (commit 2c1a035), AWAITING v81 DEPLOY
- Added (code + migration 021, tested green in jarvis/core:v81):
  yt_greg_isenberg (handle verified, 695K subs), yt_this_week_in_startups
  (seeded by channel_id UCkkhmBWfS7pILYIk0izkc3A — @thisweekinstartups is a
  1,980-sub SQUATTER, do not use the handle), gregs_letter (Substack RSS
  latecheckout.substack.com/feed; gregisenberg.com itself is a feedless Framer
  SPA), appsumo (/api/v2/deals/ JSON; per_page<=50 honoured, ordering params
  silently ignored, filter has_started AND NOT has_ended client-side).
- NOT added, probed 2026-08-16: trends_vc (Cloudflare 403 all paths from this
  IP — reddit-class block), exploding_topics (no public RSS; paid API needs a
  credential decision), indie_hackers MRR (SPA shell, needs browser transport).
  product_hunt already live.
- TubeOnAI: API DOCS NOW EXIST (they did not on 2026-08-07) —
  help.tubeonai.com "TubeOnAI API Documentation". Base https://app.tubeonai.com,
  Bearer key from web.tubeonai.com Settings > Developer Features (pk_live_*,
  shown once), POST /summaries {url, type: youtube, webhook_url...},
  GET /summaries/{id}, 60 req/min. Integration = operator gets key, install as
  JPD_TUBEONAI_KEY, then implement connector + contract_test per A1c
  (paraphrase flag mandatory; barred from claims).
- NEXT: deploy v81, then check+harvest yt_greg_isenberg, yt_this_week_in_startups,
  gregs_letter, appsumo.
