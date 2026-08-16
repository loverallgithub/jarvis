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

## STATUS
- expire_due fix: CODE DONE (console/tasks.py — success recorded on empty sweep;
  test added: test_an_empty_expiry_sweep_still_records_success in
  tests/integration/test_human_tasks.py). NOT YET BUILT/DEPLOYED.
- scheduler: design pending (waiting on CLI map from explore agent)
- recopy/launch: pending
- dedup: pending
- ServiceDown synthetic: pending
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
