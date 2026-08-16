# HT-001 — Create the JarvisProductDevelopment Telegram forum (incl. the Sintra thread)

> ## ✅ COMPLETE — 2026-08-08. All three pass criteria met.
>
> Bot **`@jpd_com_bot`** (display name `jarvis_product_development`, id `8928431992`) —
> **not** the Pimlico bot, which is `@pimlico_platform_bot` and whose webhook at
> `hermes.pimlicoservices.cloud/commands` was left untouched.
>
> `chat_id -1003909148999` · `decisions=3` `human-tasks=6` `sintra=7` `discoveries=8`
> `revenue=9` `alerts=10`
>
> 1. `jpd telegram streams` → **6/6 configured · HT-001 complete**, exit 0
> 2. All six topics probed with a real post: **6/6 reachable, each in its own thread**
> 3. Round trip **proven live** — card `JPD-B48C1A` → thread 6, operator replied in Telegram,
>    `poller.cycle accepted=1 matched=1 polled=1`, row went `status=replied`,
>    `resolved_at 16:52:28`
>
> Privacy mode was already disabled (`getMe` → `can_read_all_group_messages: true`) and the bot
> had no webhook, so `getUpdates` worked first try. **Kept for re-runs** — the steps below are
> still correct if a topic is added, the bot is replaced, or the forum is rebuilt.

| | |
|---|---|
| **Type** | Human task — blocking |
| **Blocks** | Build phase 2 (Console). Without it there is no operator surface, no human-task queue, and **no Sintra thread**. |
| **Platform** | Telegram (mobile or desktop app — **not** Telegram Web, which hides some settings) |
| **Time** | ~10 minutes |
| **You will need** | The JPD bot token (created in step 2) |

---

## Why this is needed

Pimlico's Telegram client posts every message — briefs, alerts, approvals, errors — into a
**single chat with a single `chat_id`**. There is no `message_thread_id`, so everything is one
undifferentiated stream. In practice that means an approval request that stalls a €297 build sits
between a metrics dump and a Reddit drip log, and it was missed for five days.

A Telegram **forum supergroup** gives each stream its own topic. JPD's Telegram client takes
`(chat_id, message_thread_id)` per stream from the source registry, so:

- `#decisions` never gets buried under `#alerts`
- `#human-tasks` is a queue you can actually work through
- **`#sintra` is the dedicated thread you asked for** — a clean list of prompts to paste into
  Sintra, each one replyable in place, so the reply parser can match a response to its task

---

## Steps

### 1. Create the group

1. Telegram → **New Message** → **New Group**
2. Add **any one contact** (Telegram requires ≥1 member; you can remove them afterwards)
3. Name it: **`JarvisProductDevelopment`**
4. Tap **Create**

### 2. Create the bot (skip if reusing an existing bot)

1. Open a chat with **@BotFather**
2. Send `/newbot`
3. Name: `Jarvis Product Development`
4. Username: something ending in `bot`, e.g. `jarvis_pd_bot`
5. **BotFather replies with the token** — copy it. It looks like `8123456789:AAH…`
6. Send `/setprivacy` → select your bot → **Disable**
   > **Why:** with privacy *enabled*, a bot only sees messages that @mention it. JPD's Sintra
   > thread relies on you replying with pasted output — the bot must be able to read replies that
   > do not mention it, or every Sintra task will silently time out.

### 3. Add the bot as an administrator

1. Group → tap the group name → **Administrators** → **Add Administrator**
2. Select your bot
3. Enable: **Manage Topics**, **Post Messages**, **Edit Messages**, **Delete Messages**,
   **Pin Messages**
4. Save

> **Why "Manage Topics":** without it the bot cannot post into a specific topic and every message
> lands in `General` — which is exactly the single-stream problem this task exists to solve.

### 4. Enable Topics (this converts the group to a forum supergroup)

1. Group → tap the group name → **Edit** → **Topics**
2. Toggle **Topics** → **ON**
3. Save

> Telegram converts the group to a supergroup at this point. **This changes the `chat_id`** — it
> becomes a large negative number beginning `-100…`. Do not record the chat_id before this step.

### 5. Create the six topics

In the group, tap the **topics icon** → **Create Topic**. Create each of these exactly:

| Topic name | Purpose |
|---|---|
| `decisions` | Gate approvals, price approvals, publish approvals |
| `human-tasks` | The blocking queue — everything with a verify command |
| `sintra` | **Sintra instruction cards** |
| `discoveries` | Newly promoted needs + the gate census that let them through |
| `revenue` | Orders, upgrades, fulfilments |
| `alerts` | Connector dormancy, synthetic-failure results, integrity drift |

### 6. Capture the IDs

Post **one message in each topic** (any text — `init` is fine). This is required: a topic has no
discoverable ID until it contains a message.

Then run this on the server (replace `<TOKEN>`):

```bash
curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" \
  | python3 -c '
import sys,json
seen={}
for u in json.load(sys.stdin).get("result",[]):
    m=u.get("message") or {}
    c=m.get("chat") or {}
    if c.get("type")=="supergroup":
        t=m.get("message_thread_id")
        name=(m.get("reply_to_message") or {}).get("forum_topic_created",{}).get("name")
        seen[t]=name or seen.get(t)
        print("chat_id:",c["id"]," thread_id:",t," topic:",seen[t]," text:",(m.get("text") or "")[:30])
'
```

You want, for each topic, the `chat_id` (same for all six) and its `message_thread_id`.

> **If `getUpdates` returns an empty list:** a webhook is already set on that bot token and it is
> consuming updates. Either use a different bot for JPD, or temporarily
> `curl -s "https://api.telegram.org/bot<TOKEN>/deleteWebhook"`, re-post the six messages, read
> the IDs, then re-set the webhook. **Do not delete the webhook on the Pimlico bot token** —
> that would take Pimlico's 41 Telegram commands offline.

### 7. Record them

**The token** goes in `/opt/jarvis/platform/docker/.env` (mode `600`):

```
JPD_TELEGRAM_BOT_TOKEN=<token from step 2>
```

Then propagate it — **`docker service update --force` does NOT re-read `env_file`**:

```bash
docker service update --env-add JPD_TELEGRAM_BOT_TOKEN=<token> jarvis_console
```

**The chat and thread ids are DATA, not config.** They live in the
`telegram_streams` table, so adding or moving a topic never needs a redeploy:

```bash
jpd telegram configure decisions   --chat-id -100… --thread-id <id>
jpd telegram configure human-tasks --chat-id -100… --thread-id <id>
jpd telegram configure sintra      --chat-id -100… --thread-id <id>
jpd telegram configure discoveries --chat-id -100… --thread-id <id>
jpd telegram configure revenue     --chat-id -100… --thread-id <id>
jpd telegram configure alerts      --chat-id -100… --thread-id <id>
```

---

## Verification — do not mark this done without it

```bash
jpd telegram streams        # expect: 6/6 configured · HT-001 complete   (exit 0)
jpd telegram contract-test  # expect: telegram is now live               (exit 0)
```

`contract-test` deliberately fails if **any** stream is missing a `thread_id`, because a bot
that authenticates but has no topic ids posts nothing anywhere — which is indistinguishable
from silence.

Then prove the round trip, which is what the whole design depends on:

```bash
jpd tasks list              # note the REF of an open task, then REPLY TO ITS CARD in Telegram
jpd tasks list              # ~25s later: the task is gone
```

> 🔴 **Do NOT run `jpd telegram poll`.** Once the console is deployed it runs its own
> always-on poll loop (`console_app._poll_loop`) which **owns `getUpdates`**. Telegram permits
> exactly one long-poll per token, so a second caller makes **both** fail with
> `409 Conflict: terminated by other getUpdates request`. Observed 2026-08-08: the CLI call and
> the console poller knocked each other out. The console recovers on its next cycle — the loop
> swallows errors by design, because the console is what tells you about outages — but you have
> to wait for it. `jpd telegram poll` remains useful only when the console is **not** running.
>
> **The poller logs nothing on idle cycles** (`poll_once` returns early when there are no
> updates), so a silent log is **not** a dead poller. Prove liveness with either:
>
> ```bash
> # heartbeat — should be seconds old
> psql … -c "SELECT now() - last_success_at FROM job_registry WHERE job_name='console.poll_replies';"
> # or the counter, which climbs every cycle
> curl -s localhost:8905/metrics | grep poll_cycles
> ```

**Pass criteria — all three:**
1. `jpd telegram streams` exits 0 and says **HT-001 complete**
2. A posted card appears **in its own topic**, not in `General`
3. **Replying to a card in Telegram resolves the task** — `jpd tasks list` no longer shows it
   *(criterion 3 is the one that matters: if the bot cannot see your replies, every Sintra task
   will expire silently. It is also what `/setprivacy → Disable` in step 2.6 exists for.)*

> **You are not blocked on this.** `jpd tasks reply <REF> "<answer>"` answers any task from
> the CLI, and it is the deliberate fallback for exactly this situation — an operator surface
> with a single route in has a single point of failure. HT-001 makes the queue *convenient*
> and reachable from a phone; it is not what makes it *work*.

---

## Failure modes seen before

| Symptom | Cause | Fix |
|---|---|---|
| Message lands in `General` | `message_thread_id` omitted or wrong | Re-read IDs (step 6) |
| `Bad Request: TOPIC_CLOSED` | Topic was closed in the UI | Reopen the topic |
| `Bad Request: not enough rights` | Bot lacks **Manage Topics** | Step 3 |
| Bot never sees your replies | Privacy mode still enabled | Step 2.6 — `/setprivacy` → Disable |
| `getUpdates` empty | A webhook is consuming updates | See note in step 6 |
| `chat_id` rejected | Recorded before Topics was enabled | Re-read after step 4 |
| `409 Conflict: terminated by other getUpdates request` | Two long-polls on one token — almost always `jpd telegram poll` racing the console's own loop | Stop running `jpd telegram poll`; the console owns `getUpdates`. It self-recovers next cycle |
| Poller log is silent for minutes | **Not a fault.** `poll_once` logs nothing when there are no updates | Check `job_registry.last_success_at` or `jpd_console_poll_cycles_total` before assuming it died |
