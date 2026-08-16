"""G4 — tell the buyer, and make failing to tell them visible.

"Every notification failure is a **metric**, not a log line."

A buyer who paid and never heard from us is the worst outcome this system can
produce, and it is exactly the kind of failure that generates no error: the
payment succeeded, the entitlement exists, the artifact is on disk — only the
email never went. Nothing is broken enough to alert on unless the attempt
itself is recorded.

So every send writes a `notifications` row first, and the row's terminal status
is the metric. A notification with no row is a notification that was never
attempted; a row stuck in `pending` is as actionable as one marked `failed`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import structlog

from .. import db
from ..connectors.base import state_of

log = structlog.get_logger("commerce.notify")

MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class Notification:
    id: int
    status: str
    detail: str = ""


async def queue(*, order_id: Optional[int], entitlement_id: Optional[int],
                kind: str, channel: str) -> int:
    return int(await db.fetchval(
        """
        INSERT INTO notifications (order_id, entitlement_id, kind, channel, status)
        VALUES ($1,$2,$3,$4,'pending') RETURNING id
        """,
        order_id, entitlement_id, kind, channel))


async def mark_sent(notification_id: int) -> None:
    await db.execute(
        "UPDATE notifications SET status='sent', sent_at=now(), attempt=attempt+1 "
        "WHERE id=$1", notification_id)


async def mark_failed(notification_id: int, error: str) -> None:
    await db.execute(
        "UPDATE notifications SET status='failed', attempt=attempt+1, error=$2 "
        "WHERE id=$1", notification_id, error[:500])
    log.error("notify.failed", notification_id=notification_id, error=error[:200])


async def send_delivery(entitlement_id: int, order_id: Optional[int],
                        links: list[dict], *, base_url: str = "") -> Notification:
    """Send the buyer their download links, over the first live channel.

    Phase 2 wired the channels (`console/channels.py`). If NONE is live, the
    notification is recorded as `skipped_dormant` — not as sent, and not as a
    silent success. The buyer still has an entitlement and the links still
    exist; what is missing is that they were told, and that stays visible and
    counted as **owed** until it is fixed.
    """
    from ..console import channels

    nid = await queue(order_id=order_id, entitlement_id=entitlement_id,
                      kind="delivery", channel="auto")

    row = await db.fetchrow(
        """
        SELECT o.buyer_email, e.buyer_ref, s.title
          FROM entitlements e
          JOIN solutions s ON s.id = e.solution_id
     LEFT JOIN orders o ON o.id = e.order_id
         WHERE e.id = $1
        """, entitlement_id)
    if row is None:
        await mark_failed(nid, f"entitlement {entitlement_id} not found")
        return Notification(nid, "failed", "entitlement not found")

    if not links:
        # Nothing was delivered, so there is nothing to announce — but the
        # obligation is real and must not read as a completed notification.
        await db.execute(
            "UPDATE notifications SET status='failed', error=$2 WHERE id=$1",
            nid, "no delivered links to send — fulfilment produced nothing")
        return Notification(nid, "failed", "nothing to deliver")

    try:
        res = await channels.send_buyer_delivery(
            buyer_email=row["buyer_email"], buyer_ref=row["buyer_ref"],
            solution_title=row["title"], links=links, base_url=base_url)
    except channels.ChannelError as e:
        await db.execute(
            "UPDATE notifications SET status='skipped_dormant', error=$2 WHERE id=$1",
            nid, str(e)[:500])
        log.warning("notify.skipped_dormant", notification_id=nid,
                    entitlement_id=entitlement_id, detail=str(e)[:200],
                    hint="the buyer has NOT been told — this is an open obligation")
        return Notification(nid, "skipped_dormant", str(e)[:200])
    except Exception as e:                                       # noqa: BLE001
        await mark_failed(nid, f"{type(e).__name__}: {e}")
        return Notification(nid, "failed", str(e)[:200])

    await db.execute(
        "UPDATE notifications SET status='sent', sent_at=now(), attempt=attempt+1, "
        "channel=$2 WHERE id=$1", nid, res.channel)
    log.info("notify.sent", notification_id=nid, entitlement_id=entitlement_id,
             channel=res.channel, links=len(links))
    return Notification(nid, "sent", res.channel)


async def pending_and_failed() -> dict[str, int]:
    """The numbers that should be alerted on.

    `skipped_dormant` counts as OWED: the money was taken and the buyer was not
    told. Folding it into "not an error" is how it would disappear.
    """
    rows = await db.fetch(
        "SELECT status, count(*) AS n FROM notifications GROUP BY status")
    out = {r["status"]: int(r["n"]) for r in rows}
    out["owed"] = out.get("pending", 0) + out.get("failed", 0) + out.get("skipped_dormant", 0)
    return out
