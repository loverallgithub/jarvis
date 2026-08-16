"""commerce.notification_retry — the registry row that had no driving code.

A buyer who paid and was not told is an open obligation. Until 2026-08-16 the
job existed only as a row in job_registry: `pending_and_failed()` counted the
debt, the owed gauge exported it, and nothing ever retried it — so the row sat
overdue forever and the debt sat owed forever, each reporting the other.

The retry re-uses the SAME notification row (attempt counts are the row's
history, not a parallel one) and must re-mint tokens: they are stored hashed,
so the plaintext from the failed attempt no longer exists anywhere.
"""
from __future__ import annotations

import pytest

from jarvis import db
from jarvis.commerce import notify
from jarvis.runtime import scheduler


async def _owed(status: str = "failed") -> tuple[int, int]:
    """A paid buyer whose delivery notification is owed."""
    need = await db.fetchval(
        "INSERT INTO needs (title) VALUES ('__retry__') RETURNING id")
    sol = await db.fetchval(
        "INSERT INTO solutions (need_id, title) VALUES ($1,'__retry__') RETURNING id",
        need)
    off = await db.fetchval(
        "INSERT INTO offers (solution_id, tier, price_minor, live) "
        "VALUES ($1,'roadmap',100,TRUE) RETURNING id", sol)
    order = await db.fetchval(
        "INSERT INTO orders (offer_id, buyer_ref, amount_minor, currency, provider, "
        "provider_ref, signature_valid, amount_matched, status) "
        "VALUES ($1,'__retry__',100,'EUR','stub',$2,TRUE,TRUE,'verified') RETURNING id",
        off, f"retry_{need}")
    ent = await db.fetchval(
        "INSERT INTO entitlements (order_id, buyer_ref, solution_id, tier) "
        "VALUES ($1,'__retry__',$2,'roadmap') RETURNING id", order, sol)
    nid = await db.fetchval(
        "INSERT INTO notifications (entitlement_id, order_id, kind, channel, "
        "status, error) VALUES ($1,$2,'delivery','auto',$3,'first send failed') "
        "RETURNING id", ent, order, status)
    return int(ent), int(nid)


def _wire_live_channel(monkeypatch, sent_log: list) -> None:
    """A live channel that records what it was asked to send."""
    from jarvis.console import channels

    async def available():
        return [{"channel": "telegram", "connector": "telegram", "live": True}]

    async def send(*, buyer_email, buyer_ref, solution_title, links, base_url=""):
        sent_log.append({"buyer_ref": buyer_ref, "links": links})
        return channels.SendResult("telegram", True)

    monkeypatch.setattr(channels, "available", available)
    monkeypatch.setattr(channels, "send_buyer_delivery", send)


async def test_an_owed_notification_is_resent_on_the_same_row(clean_db, monkeypatch):
    ent, nid = await _owed("failed")
    sent: list = []
    _wire_live_channel(monkeypatch, sent)

    async def fake_relink(entitlement_id):
        assert entitlement_id == ent
        return [{"tier": "roadmap", "artifact_id": 1,
                 "token": "fresh", "expires_at": None}]
    monkeypatch.setattr(notify, "_relink", fake_relink)

    out = await notify.retry_owed()
    assert out["examined"] == 1 and out["sent"] == 1

    row = await db.fetchrow(
        "SELECT status, attempt FROM notifications WHERE id=$1", nid)
    assert row["status"] == "sent"
    assert row["attempt"] == 1, "the retry belongs to the row's OWN history"
    assert len(sent) == 1
    # Same row, not a parallel one — a second notification would double-count
    # the obligation in `owed` while looking like progress.
    assert await db.fetchval("SELECT count(*) FROM notifications") == 1


async def test_retry_refuses_when_no_channel_is_live(clean_db, monkeypatch):
    """Retrying into channels known to be down would burn the attempt budget
    while nothing can possibly send — and then stop for ever once a channel
    finally came up."""
    from jarvis.console import channels
    ent, nid = await _owed("failed")

    async def none_live():
        return [{"channel": "telegram", "connector": "telegram", "live": False}]
    monkeypatch.setattr(channels, "available", none_live)

    out = await notify.retry_owed()
    assert out.get("no_live_channel")
    row = await db.fetchrow(
        "SELECT status, attempt FROM notifications WHERE id=$1", nid)
    assert row["status"] == "failed" and row["attempt"] == 0


async def test_exhausted_attempts_are_left_alone(clean_db, monkeypatch):
    ent, nid = await _owed("failed")
    await db.execute("UPDATE notifications SET attempt=5 WHERE id=$1", nid)
    sent: list = []
    _wire_live_channel(monkeypatch, sent)

    out = await notify.retry_owed()
    assert out["examined"] == 0
    assert not sent


async def test_no_delivered_fulfilment_marks_failed_and_spends_an_attempt(
        clean_db, monkeypatch):
    """Nothing to re-link is a FAILED retry, not a skipped one — the buyer is
    still owed and the attempt budget is what stops this repeating for ever."""
    ent, nid = await _owed("skipped_dormant")
    sent: list = []
    _wire_live_channel(monkeypatch, sent)

    out = await notify.retry_owed()
    assert out["examined"] == 1 and out["sent"] == 0
    row = await db.fetchrow(
        "SELECT status, attempt FROM notifications WHERE id=$1", nid)
    assert row["status"] == "failed" and row["attempt"] == 1
    assert not sent


async def test_the_scheduler_job_stamps_success_even_with_nothing_owed(clean_db):
    """The sweep RAN — the empty-expiry-sweep rule. The debt itself is watched
    by the owed gauge and BuyerNotNotified, not by this job's staleness."""
    await db.execute(
        "INSERT INTO job_registry (job_name, expected_interval_s) VALUES ($1, 900) "
        "ON CONFLICT (job_name) DO UPDATE SET last_success_at = NULL",
        "commerce.notification_retry")
    await scheduler._run_notification_retry()
    age = await db.fetchval(
        "SELECT now() - last_success_at FROM job_registry "
        "WHERE job_name = 'commerce.notification_retry'")
    assert age is not None and age.total_seconds() < 60
