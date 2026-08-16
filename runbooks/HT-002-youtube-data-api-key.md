# HT-002 — YouTube Data API v3 key

| | |
|---|---|
| **Type** | Human task — **blocking for the entire `authority` source type** |
| **Platform** | Google Cloud Console |
| **Time** | ~10 minutes. No card required |
| **Cost** | Free. 10,000 quota units/day |
| **Verify with** | `jpd connectors check youtube_data_v3` → `live` |

---

## What this unblocks

Six `sources` rows are enabled today with nothing able to harvest them —
`jpd connectors orphans` listed them as *"rows that can never emit"*:

`yt_alex_hormozi` · `yt_leila_hormozi` · `yt_codie_sanchez` ·
`yt_liam_ottley` · `yt_liam_evans` · `yt_jack_roberts`

That is the **whole `authority` source type**. The connector code now exists
(`connectors/sources.py`, `YouTubeChannel`); only the credential is missing.

⚠️ `authority` **cannot self-corroborate** — the cross-source gate needs ≥2
distinct source types, and six channels are one type. This widens the funnel's
input; it does not on its own promote anything.

---

## Steps

1. <https://console.cloud.google.com> → **Select a project** → **New Project**.
   Name it `jarvis-product-development`.
2. **APIs & Services → Library** → search **YouTube Data API v3** → **Enable**.
   *Enabling is a separate act from creating the key. A key without this
   returns 403 `accessNotConfigured`, which reads exactly like a bad key.*
3. **APIs & Services → Credentials → Create Credentials → API key.**
4. **Restrict the key** (do not skip): **API restrictions → Restrict key →
   YouTube Data API v3**. Leave application restrictions as *None* — this is a
   server-side call from a VPS with no referrer.
5. Copy the key. It starts `AIza`.

> **Quota.** 10,000 units/day is the default. The connector takes the cheap
> path deliberately: `channels.list` + `playlistItems.list` = **2 units per
> channel**, so all six cost **12 units**. The `search.list` resolution path
> costs **100 units** and is opt-in per channel for exactly this reason.

---

## Install it

```bash
# 1. JPD env (mode 600, never git-tracked)
printf 'JPD_YOUTUBE_API_KEY=%s\n' 'AIza...' >> /opt/jarvis/platform/docker/.env
chmod 600 /opt/jarvis/platform/docker/.env

# 2. both services — core harvests, console runs the CLI and its probes
#    drive dormancy
docker service update --env-add JPD_YOUTUBE_API_KEY='AIza...' jarvis_core
docker service update --env-add JPD_YOUTUBE_API_KEY='AIza...' jarvis_console

# 3. deploy the connector code
/opt/jarvis/platform/docker/deploy.sh
```

### Then tell each source WHICH channel it is

🔴 **The connector refuses to guess.** A connector that harvests the wrong
channel is worse than a dormant one — the wrong channel still yields signals
and every gate downstream believes them. Identity is DATA, so this is an
`UPDATE`, not a redeploy.

Read each handle off the channel's own URL (`youtube.com/@Handle`) and paste it
in. **Do not guess a handle from the display name** — a wrong handle returns an
empty result set, not an error.

```bash
docker exec -i $(docker ps -q -f label=com.docker.swarm.service.name=jarvis_postgres) \
  psql -U jarvis -d jarvis <<'SQL'
UPDATE sources SET config = config || '{"handle":"@PASTE_HANDLE"}'::jsonb WHERE name='yt_alex_hormozi';
UPDATE sources SET config = config || '{"handle":"@PASTE_HANDLE"}'::jsonb WHERE name='yt_leila_hormozi';
UPDATE sources SET config = config || '{"handle":"@PASTE_HANDLE"}'::jsonb WHERE name='yt_codie_sanchez';
UPDATE sources SET config = config || '{"handle":"@PASTE_HANDLE"}'::jsonb WHERE name='yt_liam_ottley';
UPDATE sources SET config = config || '{"handle":"@PASTE_HANDLE"}'::jsonb WHERE name='yt_liam_evans';
UPDATE sources SET config = config || '{"handle":"@PASTE_HANDLE"}'::jsonb WHERE name='yt_jack_roberts';
SQL
```

The first successful harvest caches `channel_id` and `uploads_playlist_id` back
into the same `config`, so every later run takes the 1-unit path.

*Alternative:* if you would rather not look up six handles, set
`'{"allow_search_resolve":true}'` instead and the connector will resolve each
name once at 100 units (600 total, one time) and cache the result. It is opt-in
because an unattended 100-unit call per harvest exhausts the daily quota in
sixteen runs and then presents as a dead connector.

---

## Verification — do not mark this done without it

```bash
jpd connectors check youtube_data_v3    # the KEY itself → expect: live
jpd connectors check yt_alex_hormozi    # one channel    → expect: live
jpd connectors harvest yt_alex_hormozi  # expect: > 0 signals stored
jpd connectors                          # expect: 10 live → 17
```

**Pass criteria:**
1. `youtube_data_v3` reaches `live` — this is the credential, checked
   independently of any channel so a key problem cannot be misread as a
   channel problem.
2. At least one `yt_*` reaches `live`. A passing probe is **not** enough; the
   contract test must parse real `playlistItems` shape.
3. `jpd connectors orphans` no longer lists any `yt_*` under
   `rows_without_code`.

### Reading a 403

All four of these are 403 and need different fixes. The connector surfaces
Google's `reason` field precisely so you do not have to guess:

| reason | fix |
|---|---|
| `accessNotConfigured` | step 2 — the API is not enabled on the project |
| `keyInvalid` | wrong key, or restricted to the wrong API (step 4) |
| `quotaExceeded` | out of units for the day; resets at midnight PT |
| `ipRefererBlocked` | application restrictions are set; clear them (step 4) |

---

## ⚠️ What this does NOT give you

The connector implements **step 1 of 03-PIPELINE §A1b** — the uploads playlist
→ video-level signals (title, description, publish time, creator captured as a
`voice` per DEC-004).

**Steps 2–4 are not built:** captions → whisper transcription → schema'd LLM
extraction of a timestamped `evidence_quote`. Until they are, an authority
signal can **promote a need but cannot back a published claim** — the same
restriction DEC-003 puts on TubeOnAI, for the same reason: a title is not a
verbatim statement of a problem.

Step 3 needs the Anthropic budget, which is capped until the limit is raised.
