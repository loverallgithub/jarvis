# Recovery checkpoint — Claude session 2026-08-16 (operator: admin@pimlicoservices.com)

If this session is lost/compressed, resume from here. Update the STATUS lines as work completes.

## Audit conclusions (done, verified)
- Telegram healthy: bot @jpd_com_bot, 6/6 streams, poller alive, contract-test ok.
  HT-001 / JPD-B48C1A resolved 2026-08-08 16:52 — the Telegram card is stale.
- AlertNeverTripped: synthetics are operator-run; last ran Aug 7. I ran
  `jpd alerts synthetics` on 2026-08-16 → 10/11 fired. Only ServiceDown has no synthetic.
- ConnectorRegressed = anthropic (API monthly cap reached; resets 2026-09-01 00:00 UTC;
  probe 200, messages → 400 "usage limits") + indie_hackers (RSS feed gone from site — needs rewrite).
- reddit fail=866: 403 unauthenticated JSON from datacenter IP → needs Reddit OAuth app creds.
- youtube family fail≈772: JPD_YOUTUBE_API_KEY absent (HT-002 setup task).
- Pipeline state: need 13 has 3/3 artifacts offerable, 34/34 claims supported, offers live
  (nevasca.pro checkout 200), sales page PUBLISHABLE 96.4% cited.
  Run 24 failed at market.copy: instructions/faq block 50% vs 90% floor.
  Resume point: decide-whether-product-self-description-counts-as-uncited.
- Needs 9..14 are duplicates: {9,11,13} same cluster, {10,12,14} same cluster.
- Bug found: console/tasks.py expire_due() returns [] before updating job_registry →
  console.expire_tasks never records success. Similar: commerce.notification_retry,
  integrity.manifest_check never record success.
- Stack: swarm services jarvis_{core,console}=jarvis/core:v77, commerce=jarvis/core:v4 (pinned),
  deploy via /opt/jarvis/platform/docker/deploy.sh, env /opt/jarvis/platform/docker/.env.

## Work plan (this session)
1. [ ] Fix expire_due early-return bug (+ record success for the always-run path). Tests.
2. [ ] Autonomous scheduler running due jobs per job_registry intervals
       (funnel/dossier/forge/synthetics/artifact_sweep...). Approach TBD in session.
3. [ ] Self-description claims decision (recommendation prepared) → jpd market recopy 13
       --below-floor → jpd market launch 13 (check what launch sends first; mailgun key absent).
4. [ ] Discovery dedup on promotion.
5. [ ] ServiceDown synthetic.
6. [ ] OpenRouter next-week spend estimate (from llm_usage), Anthropic cap explanation,
       interactive key collection (YouTube API key, Reddit OAuth, Mailgun...) with
       beginner instructions.
7. Deploy: bump image tag, deploy core+console (commerce stays v4 unless needed), verify
   jpd doctor + alerts synthetics + job ages recover.

## STATUS (updated by resumed session, 2026-08-16 — SECOND UPDATE, post-deploy)

ALL SEVEN WORK-PLAN ITEMS LANDED except the operator-interactive key
collection (item 6, still open). Verified live 2026-08-16 ~07:15 UTC:
- v78 DEPLOYED (image 2b367e11e06d, verified by ID; commerce stays v4).
  Suite green in the deployed image: ~710 tests, 4 skips. Migrations 017+018
  applied. jpd doctor: all checks passed.
- Scheduler timer ENABLED (jarvis-scheduler.timer, */15). First tick ran
  07:15. console.expire_tasks now stamps (07:12). integrity.manifest_check
  DISABLED by migration 018; commerce.notification_retry now has a driver
  (notify.retry_owed via scheduler DISPATCH; retries capped at 5 attempts,
  refuses when no channel is live; tests in test_notification_retry.py).
- OPERATOR DECISIONS 2026-08-16: (a) carve-out extended to offer descriptions
  — is_offer_description() in forge/verify.py: audience-targeting sentences,
  and price sentences ONLY when every stated amount matches offers.price_minor
  (wrong price still fails); callers pass the ladder via market/copy.py
  _offer_prices_minor() and pages.page_state(). (b) needs 9-12 KEPT promoted
  (operator accepted the scheduler researching/forging them — first tick
  started a dossier on one). (c) new `jpd market remeasure <need>` re-scores
  stored blocks for free (measurement changed, copy did not).
- NEED 13 PAGE IS **PUBLISHABLE**: remeasure cleared instructions/faq
  (50→100), then five blocks carrying author-note placeholders
  ([NEEDS PRICING], [claim needed…], [needs checking/verification]) were
  surgically recopied (~$1.25 via OpenRouter fallback, haiku-4.5): roadmap/faq,
  roadmap/benefits, instructions/subhead, instructions/objections (×2 —
  first regen came back 50%), deployed/subhead. Page: 3 tiers, 3 live offers,
  100% cited, no placeholders, need-13-5b9ca2e4b520.html.
- market.launch 13: REFUSED as designed — 5/5 voices community-scraped,
  do_not_contact, no lawful basis. Outreach waits on a human recording a
  lawful basis per voice; mailgun key only matters at real send time.
- Synthetics: 11/11 fire, ServiceDown included (was 10/11).
- Git repo now exists at /opt/jarvis (created for /ultrareview; initial commit
  09eba02 predates this session's changes — working tree is AHEAD of it).

## PREVIOUS STATUS (superseded, kept for the audit trail)
- expire_due fix: CODE DONE (console/tasks.py — success recorded on empty sweep;
  test added: test_an_empty_expiry_sweep_still_records_success in
  tests/integration/test_human_tasks.py). BUILT INTO v78; deploy pending operator.
- scheduler: CODE DONE — runtime/scheduler.py + migration 017 (last_attempt_at)
  + tests/integration/test_scheduler.py + `jpd scheduler tick|status` +
  host units /etc/systemd/system/jarvis-scheduler.{service,timer} (15-min tick,
  flock + jpd only). Timer is INSTALLED BUT DISABLED — do not enable until
  v78 is deployed AND needs 9-12 are parked (else it spends on duplicates).
- recopy/launch: BLOCKED ON OPERATOR DECISION (carve-out extension, see below).
  Verified: only ONE block below floor — instructions/faq at 50% (2 checkable);
  failing sentences are the €40 price sentence (matches offers.price_minor=4000
  exactly) and the audience sentence. market.launch never sends: plans, then
  refuses (0 contactable) or stops for a human decision — mailgun only needed
  at real send time.
- dedup: CODE DONE — funnel skips promotion when a cluster's title token-set
  matches an existing non-parked need, reattaching voices to the existing need
  (funnel.py ~l.160; stage test asserts second run mints no duplicate).
  DATA CLEANUP PENDING: needs 9,10,11,12 still 'promoted' (no dossier, no
  voice_mentions) — park them before enabling the scheduler timer:
    UPDATE needs SET status='parked' WHERE id IN (9,10,11,12) AND status='promoted';
- ServiceDown synthetic: CODE DONE (_syn_service_down covers the
  Alertmanager→console→Telegram delivery path; up==0 half stays covered by the
  phase-2 C7 exercise).
- v78: BUILT 2026-08-16, image fb4ed1ef86ca (v77 is b2a0beaaa2e0 — genuinely
  new). FULL SUITE GREEN INSIDE v78 against jarvis_test: ~696 passed, 4 skipped,
  exit 0. Deploy (./deploy.sh v78) + `systemctl enable --now
  jarvis-scheduler.timer` NOT RUN — blocked on operator permission.
- Orphan jobs noted (no driver anywhere in code): commerce.notification_retry,
  integrity.manifest_check — enabled in job_registry, last_success_at NULL
  forever; shown red in `jpd scheduler status` as "app-loop or unmapped". No
  alert fires on them (job-staleness alerts are per-job, not generic). Decide:
  implement in scheduler DISPATCH or disable-with-reason.
- spend/keys: DATA GATHERED —
  OpenRouter: account $120 credits, $98.52 used (~$21.48 left); key limit $15/day;
  usage_weekly $0.156, usage_monthly $7.17 (whole build month of heavy runs cost ~$7).
  Anthropic: monthly usage cap on the account, resets 2026-09-01 00:00 UTC;
  llm_usage table is EMPTY (cost tracking never populated — worth noting).
- Deploy plan: next image tag v78 (services currently run v77 despite checkpoint
  saying v61); ./deploy.sh v78 builds+deploys core+console, commerce stays v4.

## Additional facts learned
- Self-description decision: v77 ALREADY ships is_product_self_description()
  (forge/verify.py:353) excluding deliverable-metric sentences (pages/minutes).
  The remaining failing sentence in need-13 instructions/faq is the AUDIENCE
  targeting sentence ("This is for owner-operators... (1–15 employees...)") —
  and price sentences ("€40 ... one-time purchase") are also still counted.
  RECOMMENDATION (to confirm with operator): extend carve-out to
  offer-descriptions (audience targeting + this-product price), validating
  price mechanically against offers.price_minor instead of research citations.
- Keys wiring: /opt/jarvis/platform/docker/.env holds JPD_* values; stack file
  interpolates them; service update --force does NOT re-read env_file — use
  deploy.sh or --env-add. Expected keys list: config.py:105-108.
- reddit connector (connectors/sources.py:841) is a stub that raises — needs
  OAuth implementation (client id+secret via new env vars, e.g.
  JPD_REDDIT_CLIENT_ID/JPD_REDDIT_CLIENT_SECRET) once operator provides creds.
- OpenRouter fallback model resolution works (forge.openrouter_model_resolved
  routed=anthropic/claude-haiku-4.5).
