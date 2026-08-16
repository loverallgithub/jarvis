# RECOVERY — 2026-08-16 source-expansion session (write once, resume here)

**Protocol reminder (jpd-resume-protocol):** do not trust the STATUS lines below
until the acceptance predicates in §4 have been re-run. They were true at
~11:10 UTC 2026-08-16 and services drift.

Branch: `session/2026-08-16-scheduler-and-carveout`
HEAD at checkpoint: `655057b` (then `2c1a035`, `53389f6`, `5ef1422` this session)

---

## 1. THE ONE THING TO DO FIRST

**Deploy v81.** The image is built and unit-tested green (126 tests, run
in-image against the test DB), migration 021 is written, code is committed —
but NOT deployed. The four newest sources exist only in the v81 image.

    $ /opt/jarvis/platform/docker/deploy.sh v81        # operator runs this
                                                       # (Claude's sandbox blocks deploy.sh)

Then wake + validate the four new connectors (a wrong feed/id shows up ONLY
under harvest, not probe):

    $ /opt/jarvis/bin/jpd connectors check yt_greg_isenberg
    $ /opt/jarvis/bin/jpd connectors check yt_this_week_in_startups
    $ /opt/jarvis/bin/jpd connectors check gregs_letter
    $ /opt/jarvis/bin/jpd connectors check appsumo
    $ /opt/jarvis/bin/jpd connectors harvest <each of the four>   # expect >0 signals each

New connectors seeded dormant-by-default get woken by `check`; harvest before
check reports "not called — connector is dormant" (hit this twice already).

---

## 2. WHAT IS DEPLOYED AND VERIFIED (as of v80)

- **30 yt_* + feed connectors, 42/55 live, 0 yt_ orphans** at last count.
- Authority roster of 28 YouTube channels ALL live and harvested (~690
  admissible signals): original 6 + batch 1 (nick_saraev, my_first_million,
  migration 019) + batch 2 (20 channels, migration 020). Every channel has
  uploads_playlist_id cached → 1-unit quota path.
- **YouTube key**: installed, live. `.env` line-23 corruption REPAIRED
  (operator's paste had appended ` jarvis_core` after the key — if `source
  .env` ever fails again, look for that pattern first).
- **forge reverify via OpenRouter fallback** (Anthropic cap holds until
  2026-09-01; fallback is automatic, no config):
  - Need 13: 14/14 supported → **3/3 tiers OFFERABLE**.
  - Need 14: 7/14 supported → **0/3 offerable, withheld**. NOT verifier
    breakage: all 7 failures are 'gap' (negative) claims; claims 5 & 7 are
    CONTRADICTED by their own sources, the other 5 are unverifiable-by-
    construction ("absence of X can't be proven from an excerpt").
    Next move for 14 is `forge repair` / regeneration, NOT another reverify.

## 3. WHAT v81 ADDS (built + committed, awaiting deploy — see §1)

- `yt_greg_isenberg` — handle @gregisenberg verified via API (695K subs).
- `yt_this_week_in_startups` — seeded by **channel_id
  UCkkhmBWfS7pILYIk0izkc3A**. 🔴 @thisweekinstartups is a 1,980-sub SQUATTER
  channel; never re-point this row at the handle.
- `gregs_letter` — RssSource on https://latecheckout.substack.com/feed
  (gregisenberg.com is a feedless Framer SPA; newsletter lives on Substack).
- `appsumo` — HttpSource on https://appsumo.com/api/v2/deals/ JSON.
  per_page honoured to 50; `ordering`/`sort` params silently IGNORED; filter
  `has_started AND NOT has_ended AND is_active` client-side. `/rss/` there is
  a 200-with-HTML SPA-shell trap.
- Migration `021_scan_sources_expansion.sql` seeds all four rows (identity
  included — no manual UPDATE step after deploy).

**Requested but NOT addable (probed 2026-08-16, don't re-probe blind):**
- trends_vc — Cloudflare 403 on every path from this IP (reddit-class).
- exploding_topics — no public RSS anywhere; paid API = credential decision.
- indie_hackers MRR — SPA shell (see IndieHackers docstring post-mortem);
  needs browser transport.
- product_hunt — already live, nothing needed.

## 4. ACCEPTANCE PREDICATES (re-run before trusting any of the above)

    $ docker service inspect jarvis_core --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}'
        # v80 = §1 still pending; v81 = §1 done, skip to harvest checks
    $ docker exec $(docker ps -q -f label=com.docker.swarm.service.name=jarvis_postgres) \
        psql -U jarvis -d jarvis -tc "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
        # expect 021_... after the v81 deploy, 020_... before it
    $ /opt/jarvis/bin/jpd connectors            # 42/55 live pre-v81; more after
    $ /opt/jarvis/bin/jpd connectors orphans    # no yt_* anywhere in output
    $ /opt/jarvis/bin/jpd connectors harvest yt_alex_hormozi   # >0 signals = YT stack healthy

## 5. TUBEONAI — INTEGRATION PATH DISCOVERED, BLOCKED ON OPERATOR CREDENTIAL

API docs now EXIST (they did not on 2026-08-07):
help.tubeonai.com → "TubeOnAI API Documentation: Setup, Usage, and Best Practices".
- Base: `https://app.tubeonai.com`, auth `Authorization: Bearer pk_live_*`.
- Key minted at web.tubeonai.com → Settings → Developer Features (shown ONCE).
- `POST /summaries` {url, type:"youtube", options, webhook_url},
  `GET /summaries/{id}`, `GET /usage/credits`. 60 req/min.
- OPERATOR: mint key → install as `JPD_TUBEONAI_KEY` (.env + --env-add both
  services) → tell Claude "implement the tubeonai connector".
- Constraint that survives integration (A1c / DEC-003): TubeOnAI output is a
  paraphrase → `evidence.source_kind='paraphrase'`, can promote a need, can
  NEVER back a published claim until a verbatim timestamped quote is captured.

## 6. STANDING ITEMS (unchanged from NEXT-STEPS.txt)

1. Anthropic spend cap — self-imposed, console-side; resets 2026-09-01 unless
   lifted at console.anthropic.com/settings/limits (org AND per-key).
   OpenRouter fallback covers verify/forge meanwhile.
2. Reddit OAuth — creds not installed; connector is a stub (866+ recorded
   fails). After creds: "implement the reddit OAuth connector".
3. Needs 9–12 — still promoted duplicates by operator decision; scheduler
   spends ~$1.50/each per research tick. Park with:
   UPDATE needs SET status='parked' WHERE id IN (9,10,11,12);
4. Need 14 — see §2: repair/regenerate the 7 failed gap claims.
5. Mailgun — only when real buyer email is wanted.

## 7. SESSION COMMITS (newest first)

- 655057b checkpoint + TubeOnAI docs discovery recorded in products-inventory
- 2c1a035 scan-source expansion (v81 content: 2 YT + gregs_letter + appsumo, mig 021)
- 53389f6 authority roster batch 2 (20 channels, mig 020) — deployed as v80 ✅
- 5ef1422 nick_saraev + my_first_million (mig 019) — deployed as v79 ✅
