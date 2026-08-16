# HT-007 — Add the you.com API key

| | |
|---|---|
| **Type** | Human task — **not blocking**. JPD already captures evidence without it |
| **Platform** | you.com developer portal |
| **Time** | ~10 minutes, plus card details |
| **Cost** | $0.012 per `lite` call. There is a **$100 free credit** on signup |
| **Verify with** | `jpd connectors check you_com` → `live` |

---

## What you actually get for this

Phase B works today. `research.capture` produced **25 substantive hash-verified evidence
rows across 33 domains** using DuckDuckGo, with zero uncited claims. So this is an
**upgrade, not an unblock**, and it is worth being precise about what improves:

| | DuckDuckGo (today) | you.com Research |
|---|---|---|
| Cost | free | $0.012/call (`lite`) |
| Result quality | web-wide, listicle-heavy | ranked for research intent |
| Snippets | none — we fetch every page ourselves | returned inline |
| Stability | **HTML scraping** — breaks whenever the markup changes | documented JSON API |
| Rate limits | undocumented, IP-based | documented per plan |

The one that actually matters is **stability**. DuckDuckGo is scraped HTML behind a
contract test; the day the markup changes, the connector goes dormant and Phase B stops.
That is the designed behaviour and it is still a single point of failure sitting on someone
else's page structure.

The measured evidence yield is **~50% usable** — Cloudflare interstitials, JS-only pages and
thin listicles. Better ranking should lift that, which means fewer fetches for the same
15-row bar.

---

## Steps

1. Go to **<https://api.you.com>** and sign in (Google/GitHub, or email).

2. Open **API Keys** → **Create API Key**. Name it `jarvis-product-development` so it is
   obvious later which system holds it.

3. **Copy the key immediately** — the portal shows it once. It looks like a long
   alphanumeric string.

4. Check **Billing** → confirm the **$100 free credit** is applied. At $0.012/call that is
   ~8,300 searches; a full Phase B run uses ~6.
   > Set a **spend cap** while you are there. Pimlico's OpenRouter key ran with `limit: null`
   > for weeks before anyone noticed — see CHECKPOINT §4.20.

5. Note which endpoint your plan includes. JPD targets the **Search / Research** endpoint,
   not the chat completion one.

---

## Install it

**Never paste the key into a shell that logs history, and never into a git-tracked file.**

```bash
# 1. add it to the JPD env (mode 600, not in git)
printf 'JPD_YOUCOM_KEY=%s\n' 'PASTE_KEY_HERE' >> /opt/jarvis/platform/docker/.env
chmod 600 /opt/jarvis/platform/docker/.env

# 2. tell the stack about it. BOTH services need it:
#    core runs research; console runs the `jpd` CLI, and a CLI without the
#    credential fails its probes and walks a healthy connector toward dormant.
docker service update --env-add JPD_YOUCOM_KEY='PASTE_KEY_HERE' jarvis_core
docker service update --env-add JPD_YOUCOM_KEY='PASTE_KEY_HERE' jarvis_console

# 3. switch the provider — it is a DATABASE ROW, so no redeploy
docker exec -i $(docker ps -q -f label=com.docker.swarm.service.name=jarvis_postgres) \
  psql -U jarvis -d jarvis -c \
  "UPDATE research_params SET value='you_com',
      rationale='you.com Research. Replaces the DuckDuckGo HTML scrape, which had no stable contract.'
    WHERE param='search_provider';"
```

Then add the connector class. This is the only code change, and it is small:
`src/jarvis/research/evidence.py` already defines `DuckDuckGoSearch` with
`probe()` / `contract_test()` / `search()`. A `YouComSearch` implementing the same three
methods drops straight into `connectors/registry.py` — the contract exists precisely so
this swap is one class.

---

## Verification — do not mark this done without it

```bash
jpd connectors check you_com     # expect: live   (probe + contract both pass)
jpd research run <need_id>       # expect: research.capture succeeds
jpd research evidence <need_id>  # expect: >= 15 rows marked live + substantive
```

**Pass criteria — all three:**
1. `you_com` reaches **`live`**. It cannot until `contract_test()` passes against the real
   API — a working key alone is not enough, the response *shape* must parse.
2. `jpd research run` completes with `evidence_usable >= 15` and `uncited_claims == 0`.
3. `jpd research verify <need_id>` shows **0 dead** citations.

> ⚠️ **Ask the API which endpoints your key serves.** Three guessed Anthropic model names all
> returned `404 not_found_error`, which reads exactly like a dead key — the key was fine.
> If you.com 404s, check the endpoint list before concluding the key is bad.

---

## What does NOT change

- **DuckDuckGo stays registered** as a fallback. If you.com goes dormant, the funnel degrades
  to the scraper rather than stopping. Two live search paths is strictly better than one.
- **Evidence handling is identical.** you.com returns snippets; JPD still fetches and hashes
  every page itself. A search engine's summary of a page is a *paraphrase* — the same
  restriction that applies to TubeOnAI (DEC-003). A paraphrase can point at evidence; it
  cannot be evidence.
- **The `substantive` gate still applies.** Better ranking will not stop Cloudflare
  interstitials being returned, and those still must not count.

---

## If you would rather not

Entirely reasonable. Phase B is not blocked. The honest trade is: you are choosing a free
scraped source with no contract over a paid API with one, and accepting that the connector
will go dormant without warning some day. The contract test means you will *know* when it
happens rather than silently getting zero competitors — which is the failure Pimlico lived
with for weeks.

If you skip this, the higher-value credential to chase instead is **HT-002 (YouTube Data API
v3)**, which unlocks the entire `authority` source type — seven creator channels that no
other source can substitute for.
