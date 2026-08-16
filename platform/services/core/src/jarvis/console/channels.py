"""Notification channels — closing the phase-1 gap.

In phase 1, `notify.send_delivery` had no live channel and honestly recorded
`skipped_dormant`: money taken, buyer not told, visibly. This module gives it
somewhere to send.

**Priority order, first live channel wins**, from the `notification_channels`
table:

  1. `ghl`      — GHL conversations. Proven in Pimlico 2026-07-31.
  2. `mailgun`  — fallback. Proven delivering 2026-07-31.
  3. `telegram` — operator-visible. Not a buyer channel; it is the backstop that
                  makes a delivery impossible to miss even when both buyer
                  channels are down.

🔴 **A download link is a bearer credential.** It appears in the buyer's email
and in the operator topic (a private forum), and it must never reach a log line,
an exception message, or a metric label.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

import httpx
import structlog

from .. import db
from ..connectors.base import state_of

log = structlog.get_logger("console.channels")


class ChannelError(RuntimeError):
    """Delivery over this channel failed. Raised, so the caller records it."""


@dataclass(frozen=True)
class SendResult:
    channel: str
    ok: bool
    detail: str = ""


async def available() -> list[dict]:
    """Enabled channels in priority order, annotated with live-ness."""
    rows = await db.fetch(
        "SELECT channel, connector, priority FROM notification_channels "
        "WHERE enabled ORDER BY priority")
    out = []
    for r in rows:
        out.append({"channel": r["channel"], "connector": r["connector"],
                    "priority": int(r["priority"]),
                    "live": await state_of(r["connector"]) == "live"})
    return out


async def send_buyer_delivery(*, buyer_email: Optional[str], buyer_ref: str,
                              solution_title: str, links: list[dict],
                              base_url: str = "") -> SendResult:
    """Try each live channel in priority order until one succeeds."""
    channels = await available()
    live = [c for c in channels if c["live"]]

    if not live:
        names = ", ".join(f"{c['channel']}({c['connector']})" for c in channels)
        raise ChannelError(
            f"no live notification channel — tried {names}. The buyer has NOT "
            f"been told; this is an open obligation, not a completed delivery.")

    last: Optional[str] = None
    for c in live:
        try:
            if c["channel"] == "ghl":
                await _send_ghl(buyer_ref, solution_title, links, base_url)
            elif c["channel"] == "mailgun":
                if not buyer_email:
                    last = "mailgun: no buyer email on the order"
                    continue
                await _send_mailgun(buyer_email, solution_title, links, base_url)
            elif c["channel"] == "telegram":
                await _send_telegram(buyer_email or buyer_ref, links)
            else:
                last = f"unknown channel {c['channel']!r}"
                continue
            log.info("channels.sent", channel=c["channel"], links=len(links))
            return SendResult(c["channel"], True)
        except Exception as e:                                   # noqa: BLE001
            last = f"{c['channel']}: {type(e).__name__}: {e}"
            log.warning("channels.attempt_failed", channel=c["channel"],
                        error=str(e)[:200])

    raise ChannelError(last or "every live channel failed")


def _render(solution_title: str, links: list[dict], base_url: str) -> tuple[str, str]:
    subject = f"Your download — {solution_title}"
    rows = []
    for l in links:
        url = f"{base_url.rstrip('/')}/download/{l['token']}" if base_url else l.get("token", "")
        rows.append(
            f'<li><b>{l["tier"].title()}</b> — '
            f'<a href="{url}">download</a> '
            f'<small>(link expires {l.get("expires_at")})</small></li>')
    html = (f"<p>Thank you — here is everything included in your purchase of "
            f"<b>{solution_title}</b>.</p><ul>{''.join(rows)}</ul>"
            f"<p>Each tier includes everything in the tiers below it.</p>")
    return subject, html


async def _send_ghl(contact_id: str, title: str, links: list[dict],
                    base_url: str) -> None:
    """GHL conversations.

    ⚠️ The parameter is `html=`, not `body=` — a Pimlico trap that cost a
    silent no-send.
    """
    key = os.environ.get("JPD_GHL_API_KEY", "")
    location = os.environ.get("JPD_GHL_LOCATION_ID", "")
    if not key or key == "CHANGE_ME":
        raise ChannelError("JPD_GHL_API_KEY absent")
    if not contact_id:
        raise ChannelError("no GHL contact id on the order")

    subject, html = _render(title, links, base_url)
    # httpx, not urllib: urllib's user-agent trips Cloudflare error 1010 here.
    async with httpx.AsyncClient(
            base_url="https://services.leadconnectorhq.com",
            headers={"Authorization": f"Bearer {key}", "Version": "2021-07-28",
                     "Content-Type": "application/json"}, timeout=30) as c:
        r = await c.post("/conversations/messages", json={
            "type": "Email", "contactId": contact_id, "locationId": location,
            "subject": subject, "html": html,
        })
    if r.status_code not in (200, 201):
        raise ChannelError(f"GHL -> {r.status_code}: {r.text[:200]}")


async def _send_mailgun(to_email: str, title: str, links: list[dict],
                        base_url: str) -> None:
    key = os.environ.get("JPD_MAILGUN_KEY", "")
    # ⚠️ The domain must be the .com one. Pimlico sent to the wrong domain and
    # every message was accepted and never delivered.
    domain = os.environ.get("JPD_MAILGUN_DOMAIN", "")
    sender = os.environ.get("JPD_MAILGUN_FROM", f"noreply@{domain}")
    if not key or key == "CHANGE_ME" or not domain:
        raise ChannelError("JPD_MAILGUN_KEY / JPD_MAILGUN_DOMAIN absent")

    subject, html = _render(title, links, base_url)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"https://api.eu.mailgun.net/v3/{domain}/messages",
                         auth=("api", key),
                         data={"from": sender, "to": to_email,
                               "subject": subject, "html": html})
    if r.status_code not in (200, 201):
        raise ChannelError(f"mailgun -> {r.status_code}: {r.text[:200]}")


async def _send_telegram(buyer: str, links: list[dict]) -> None:
    """Operator-visible backstop, posted to #revenue.

    Deliberately does NOT include the download URLs. The operator does not need
    the buyer's bearer tokens to know a delivery happened, and a token pasted
    into a group chat is a token that outlives its purpose.
    """
    from . import cards
    from .telegram import TelegramClient
    await TelegramClient().post(
        "revenue", cards.delivery(buyer=buyer,
                                  tiers=[l["tier"] for l in links], links=links))
