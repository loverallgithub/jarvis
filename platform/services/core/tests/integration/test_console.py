"""Console: streams, poller, channels, and the CLI fallback.

The theme is C7 — **the operator must be able to see and act even when
something is broken.** Each test names the specific breakage it survives.
"""
from __future__ import annotations

import pytest

from jarvis import db
from jarvis.console import channels, poller, tasks
from jarvis.console.telegram import (STREAMS, StreamNotConfigured, TelegramClient,
                                     configure_stream, stream_status)
from jarvis.connectors import base as connectors
from jarvis.runtime import watermark


# ---------------------------------------------------------------------------
# streams
# ---------------------------------------------------------------------------

async def test_all_six_streams_are_seeded_without_ids(clean_db):
    """Seeded WITHOUT chat ids on purpose. A placeholder id would mean posts
    silently landing in the wrong chat, which is worse than not posting."""
    rows = await stream_status()
    assert {r["stream"] for r in rows} == set(STREAMS)
    assert all(r["chat_id"] is None for r in rows)


async def test_posting_to_an_unconfigured_stream_raises_rather_than_defaulting(clean_db):
    """🔴 Falling back to the General topic IS the single-stream failure this
    whole design exists to remove. Pimlico's approval sat between a metrics dump
    and a Reddit log and was missed for five days."""
    with pytest.raises(StreamNotConfigured, match="HT-001"):
        await TelegramClient.resolve("sintra")


async def test_an_unknown_stream_is_refused(clean_db):
    with pytest.raises(StreamNotConfigured, match="unknown stream"):
        await TelegramClient.resolve("general")


async def test_configuring_a_stream_makes_it_resolvable(clean_db):
    await configure_stream("sintra", -1001234567890, 42)
    chat, thread = await TelegramClient.resolve("sintra")
    assert (chat, thread) == (-1001234567890, 42)


async def test_a_disabled_stream_is_refused(clean_db):
    await configure_stream("alerts", -100123, 7)
    await db.execute("UPDATE telegram_streams SET enabled=FALSE WHERE stream='alerts'")
    with pytest.raises(StreamNotConfigured, match="disabled"):
        await TelegramClient.resolve("alerts")


async def test_contract_test_fails_while_streams_lack_ids(clean_db):
    """Reachable is not configured. A bot that authenticates but has no topic
    ids posts nothing anywhere, which is indistinguishable from silence."""
    r = await TelegramClient(token="").contract_test()
    assert r.ok is False
    assert "token absent" in r.detail


# ---------------------------------------------------------------------------
# poller
# ---------------------------------------------------------------------------

async def test_poll_without_a_token_is_not_an_error(clean_db):
    """HT-001 may simply be outstanding. Tasks still accumulate and stay
    visible; only the reply channel is missing."""
    out = await poller.poll_once(timeout_s=1)
    assert out["skipped_no_token"] == 1
    assert out["polled"] == 0


async def test_the_update_cursor_clamps_like_every_other_watermark(clean_db):
    """Same mechanism that un-wedged Pimlico's n8n watcher, which sat at 1757
    against a real max id of 34 and reported 'no failures' for weeks."""
    await watermark.advance(poller.CURSOR, 999999, observed_max=999999)
    effective = await watermark.read(poller.CURSOR, observed_max=12)
    assert effective == 12
    row = await db.fetchrow("SELECT clamped_at FROM watermarks WHERE name=$1",
                            poller.CURSOR)
    assert row["clamped_at"] is not None


async def test_a_reply_is_matched_to_its_task_and_applied(clean_db):
    t = await tasks.create(type="task", title="t", why="w",
                           reply_schema={"type": "text", "min_chars": 5})
    await db.execute(
        "UPDATE human_tasks SET telegram_message_id=555, telegram_thread_id=42 WHERE id=$1",
        t.id)

    out = await poller._handle({
        "update_id": 1,
        "message": {"message_id": 900, "text": "here is the full answer",
                    "chat": {"id": -100123}, "message_thread_id": 42,
                    "reply_to_message": {"message_id": 555},
                    "from": {"id": 7}},
    })
    assert out == {"matched": 1, "accepted": 1}
    assert await db.fetchval("SELECT status FROM human_tasks WHERE id=$1", t.id) == "replied"


async def test_a_redelivered_update_is_not_applied_twice(clean_db):
    """Telegram redelivers anything not acknowledged by an advanced offset. A
    crash between 'processed' and 'offset advanced' would otherwise apply the
    same reply twice."""
    t = await tasks.create(type="task", title="t", why="w",
                           reply_schema={"type": "text", "min_chars": 5})
    await db.execute("UPDATE human_tasks SET telegram_message_id=555 WHERE id=$1", t.id)
    update = {"update_id": 7,
              "message": {"message_id": 900, "text": "the real answer here",
                          "chat": {"id": -100123},
                          "reply_to_message": {"message_id": 555}}}

    assert (await poller._handle(update))["accepted"] == 1
    assert (await poller._handle(update))["accepted"] == 0
    assert await db.fetchval("SELECT count(*) FROM telegram_replies") == 1


async def test_a_reply_matching_no_task_is_recorded_not_dropped(clean_db):
    """Same reasoning as provider_events on the money path: 'we never saw it'
    and 'we could not use it' must stay distinguishable."""
    out = await poller._handle({
        "update_id": 3, "message": {"message_id": 1, "text": "hello?",
                                    "chat": {"id": -100123}}})
    assert out["matched"] == 0
    row = await db.fetchrow("SELECT * FROM telegram_replies WHERE update_id=3")
    assert row["accepted"] is False
    assert "no open task matched" in row["reject_reason"]


async def test_a_thread_reply_falls_back_to_the_oldest_open_task(clean_db):
    """On mobile it is easy to type into the topic rather than onto the card.
    Oldest, not newest — it matches the order the operator sees them, and a
    mis-match is recoverable because the schema rejects and re-asks."""
    first = await tasks.create(type="task", title="first", why="w",
                               reply_schema={"type": "text", "min_chars": 5})
    await db.execute("UPDATE human_tasks SET telegram_thread_id=42 WHERE id=$1", first.id)
    second = await tasks.create(type="task", title="second", why="w",
                                reply_schema={"type": "text", "min_chars": 5})
    await db.execute("UPDATE human_tasks SET telegram_thread_id=42, "
                     "created_at=now() + interval '1 minute' WHERE id=$1", second.id)

    await poller._handle({"update_id": 9,
                          "message": {"message_id": 1, "text": "answering the first",
                                      "chat": {"id": -100123}, "message_thread_id": 42}})
    assert await db.fetchval("SELECT status FROM human_tasks WHERE id=$1",
                             first.id) == "replied"
    assert await db.fetchval("SELECT status FROM human_tasks WHERE id=$1",
                             second.id) == "open"


async def test_an_empty_message_is_recorded_and_ignored(clean_db):
    out = await poller._handle({"update_id": 4,
                                "message": {"message_id": 1, "text": "",
                                            "chat": {"id": -100123}}})
    assert out["matched"] == 0
    assert await db.fetchval(
        "SELECT reject_reason FROM telegram_replies WHERE update_id=4") == "no text"


# ---------------------------------------------------------------------------
# notification channels
# ---------------------------------------------------------------------------

async def test_channels_are_ordered_and_all_start_dormant(clean_db):
    rows = await channels.available()
    assert [c["channel"] for c in rows] == ["ghl", "mailgun", "telegram"]
    assert all(c["live"] is False for c in rows)


async def test_no_live_channel_raises_rather_than_reporting_success(clean_db):
    """Money taken and buyer not told is the worst outcome this system can
    produce. It must be loud."""
    with pytest.raises(channels.ChannelError, match="no live notification channel"):
        await channels.send_buyer_delivery(
            buyer_email="b@x.test", buyer_ref="c1", solution_title="X",
            links=[{"tier": "roadmap", "token": "t", "expires_at": "2027-01-01"}])


async def test_a_live_channel_is_attempted(clean_db):
    """ghl becomes live but has no credentials, so the attempt fails and the
    error names the channel — not a generic 'notification failed'."""
    await connectors.record_contract_test("ghl", True, "test fixture")
    with pytest.raises(channels.ChannelError, match="ghl"):
        await channels.send_buyer_delivery(
            buyer_email="b@x.test", buyer_ref="", solution_title="X",
            links=[{"tier": "roadmap", "token": "t", "expires_at": "2027-01-01"}])


async def test_the_rendered_email_includes_every_tier_and_a_real_url(clean_db):
    subject, html = channels._render(
        "Change-Order Recovery",
        [{"tier": "roadmap", "token": "TOK1", "expires_at": "2027-01-01"},
         {"tier": "instructions", "token": "TOK2", "expires_at": "2027-01-01"}],
        "https://buy.example.test")
    assert "Change-Order Recovery" in subject
    assert "https://buy.example.test/download/TOK1" in html
    assert "https://buy.example.test/download/TOK2" in html
    assert "Roadmap" in html and "Instructions" in html


# ---------------------------------------------------------------------------
# the CLI fallback — C7
# ---------------------------------------------------------------------------

async def test_a_task_can_be_answered_from_the_cli_without_telegram(clean_db, capsys):
    """🔴 An operator surface with exactly one route in has a single point of
    failure. This is how a run gets unblocked when Telegram is down — or before
    HT-001 has been done at all."""
    from jarvis.cli import cmd_tasks_reply

    t = await tasks.create(type="task", title="Store id?", why="blocks the ladder",
                           reply_schema={"type": "fields", "required": {"store_id": "str"}})

    class A:
        ref = t.ref
        text = "store_id: ABC123"

    assert await cmd_tasks_reply(A()) == 0
    assert await db.fetchval("SELECT status FROM human_tasks WHERE id=$1", t.id) == "replied"
    assert "accepted" in capsys.readouterr().out


async def test_the_cli_re_asks_on_a_bad_reply_and_exits_2(clean_db, capsys):
    from jarvis.cli import cmd_tasks_reply

    t = await tasks.create(type="task", title="t", why="w",
                           reply_schema={"type": "text", "min_chars": 100})

    class A:
        ref = t.ref
        text = "too short"

    assert await cmd_tasks_reply(A()) == 2
    assert "still open" in capsys.readouterr().out
    assert await db.fetchval("SELECT status FROM human_tasks WHERE id=$1", t.id) == "open"


async def test_the_task_list_works_on_an_empty_database(clean_db, capsys):
    from jarvis.cli import cmd_tasks_list

    class A:
        pass

    assert await cmd_tasks_list(A()) == 0
    assert "no open human tasks" in capsys.readouterr().out


async def test_telegram_streams_command_reports_ht_001_outstanding(clean_db, capsys):
    from jarvis.cli import cmd_telegram_streams

    class A:
        pass

    assert await cmd_telegram_streams(A()) == 1
    out = capsys.readouterr().out
    assert "HT-001 outstanding" in out
    assert "0/6 configured" in out

    for s in STREAMS:
        await configure_stream(s, -100123, 1)
    assert await cmd_telegram_streams(A()) == 0
    assert "HT-001 complete" in capsys.readouterr().out


async def test_channels_command_warns_when_nothing_is_live(clean_db, capsys):
    from jarvis.cli import cmd_channels

    class A:
        pass

    assert await cmd_channels(A()) == 1
    out = capsys.readouterr().out
    assert "skipped_dormant" in out
    assert "OWED" in out
