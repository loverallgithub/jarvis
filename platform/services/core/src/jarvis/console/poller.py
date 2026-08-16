"""Telegram reply poller.

Matches inbound replies to open tasks and drives them to resolution.

Three things it gets right that are easy to get wrong:

**1. The update cursor is a clamped watermark.** Reusing the same mechanism that
un-wedged Pimlico's n8n watcher, which sat at `last_seen = 1757` against a real
maximum id of 34 and reported "no failures" every day for weeks. If the bot
token is swapped or Telegram resets ids, the cursor self-heals instead of
blinding the console forever.

**2. Every candidate reply is recorded before it is interpreted**, including
ones that match no task and ones that fail to parse. Same reasoning as
`provider_events` on the money path.

**3. `update_id` is UNIQUE.** Telegram redelivers anything not acknowledged by
an advanced offset, so a crash between "processed" and "offset advanced" would
otherwise apply the same reply twice.
"""
from __future__ import annotations

from typing import Any, Optional

import structlog

from .. import db
from ..runtime import watermark
from . import tasks
from .telegram import TelegramClient, TelegramError

log = structlog.get_logger("console.poller")

CURSOR = "telegram:update_offset"


async def poll_once(timeout_s: int = 25) -> dict[str, int]:
    """One long-poll cycle. Safe to call in a loop or on a schedule."""
    client = TelegramClient()
    if not client.configured:
        # Not an error: HT-001 may simply be outstanding. Tasks still accumulate
        # and stay visible in `jpd tasks`; only the reply channel is missing.
        return {"polled": 0, "matched": 0, "accepted": 0, "skipped_no_token": 1}

    saved = await watermark.read(CURSOR, observed_max=0)
    try:
        updates = await client.get_updates(offset=saved + 1, timeout_s=timeout_s)
    except TelegramError as e:
        log.warning("poller.get_updates_failed", error=str(e)[:200])
        return {"polled": 0, "matched": 0, "accepted": 0, "error": 1}

    if not updates:
        await _mark_job_ok()
        return {"polled": 0, "matched": 0, "accepted": 0}

    observed_max = max(int(u.get("update_id", 0)) for u in updates)
    # Clamp against what this backend actually has, in case the token changed
    # underneath us and ids went backwards.
    await watermark.read(CURSOR, observed_max=observed_max)

    matched = accepted = 0
    for u in updates:
        r = await _handle(u)
        matched += r["matched"]
        accepted += r["accepted"]

    await watermark.advance(CURSOR, observed_max, observed_max=observed_max)
    await _mark_job_ok()
    log.info("poller.cycle", polled=len(updates), matched=matched, accepted=accepted,
             cursor=observed_max)
    return {"polled": len(updates), "matched": matched, "accepted": accepted}


async def _mark_job_ok() -> None:
    await db.execute(
        "UPDATE job_registry SET last_success_at = now() WHERE job_name = $1",
        "console.poll_replies")


async def _handle(update: dict[str, Any]) -> dict[str, int]:
    update_id = int(update.get("update_id", 0))
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    text = msg.get("text") or ""
    reply_to = (msg.get("reply_to_message") or {}).get("message_id")
    thread_id = msg.get("message_thread_id")
    from_user = (msg.get("from") or {}).get("id")

    async def record(task_id: Optional[int], ok: bool, reason: str = "") -> None:
        # ON CONFLICT DO NOTHING: Telegram redelivers un-acknowledged updates,
        # and applying a reply twice would resolve a task with a stale answer.
        await db.execute(
            """
            INSERT INTO telegram_replies (update_id, chat_id, thread_id,
                        reply_to_message_id, from_user_id, text_raw,
                        matched_task_id, accepted, reject_reason)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (update_id) DO NOTHING
            """,
            update_id, chat.get("id"), thread_id, reply_to, from_user,
            text[:20000], task_id, ok, reason[:500] or None)

    if not text.strip():
        await record(None, False, "no text")
        return {"matched": 0, "accepted": 0}

    # ── commands, BEFORE reply-matching ────────────────────────────────────
    # Order matters. The fallback below claims any message in a topic with an
    # open task as an ANSWER to that task, so a `/status` typed into a topic
    # while a task is open would otherwise be consumed as the reply — resolving
    # or rejecting a task the operator was not answering.
    #
    # A command is answered in the topic it came from, so /status in #alerts
    # does not spray across #decisions.
    from . import commands
    reply_text = await commands.dispatch(text)
    if reply_text is not None:
        try:
            await TelegramClient().post(None, reply_text, chat_id=chat.get("id"),
                                        thread_id=thread_id)
        except Exception as e:                                   # noqa: BLE001
            log.warning("poller.command_reply_failed", error=str(e)[:200])
        await record(None, True, f"command: {text.split()[0][:40]}")
        return {"matched": 1, "accepted": 1, "command": 1}

    task_row = None
    if reply_to:
        task_row = await db.fetchrow(
            "SELECT id, ref FROM human_tasks WHERE telegram_message_id = $1 "
            "AND status = 'open'", reply_to)

    if task_row is None and thread_id is not None:
        # Fall back to the OLDEST open task in this topic. A reply that is not a
        # threaded reply is still an answer to something, and on mobile it is
        # easy to type into the topic rather than onto the card.
        #
        # Oldest, not newest, deliberately: it matches the order the operator
        # sees them and makes the mis-match recoverable — the wrong answer is
        # rejected by the schema and re-asked, rather than silently accepted
        # against a task it does not belong to.
        task_row = await db.fetchrow(
            """
            SELECT h.id, h.ref FROM human_tasks h
             WHERE h.status='open' AND h.telegram_thread_id = $1
             ORDER BY h.created_at LIMIT 1
            """, thread_id)

    if task_row is None:
        await record(None, False, "no open task matched")
        return {"matched": 0, "accepted": 0}

    # Already-seen guard, checked BEFORE applying.
    seen = await db.fetchval(
        "SELECT 1 FROM telegram_replies WHERE update_id = $1", update_id)
    if seen:
        return {"matched": 1, "accepted": 0}

    parsed = await tasks.apply_reply(int(task_row["id"]), text)
    await record(int(task_row["id"]), parsed.ok,
                 "" if parsed.ok else (parsed.error or "parse failed"))
    return {"matched": 1, "accepted": 1 if parsed.ok else 0}
