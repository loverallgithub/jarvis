"""Card rendering.

Every card answers four questions before it asks for anything:
**what is blocked, what it costs, exactly what to do, and how to prove it worked.**

Pimlico's operator prompts were bullet lists titled `⛔ USER ACTION` with no
stated consequence. They were skipped for weeks — not out of negligence, but
because nothing on the card said what would happen if they were not done. `why`
is a NOT NULL column for that reason, and it is rendered second, right under the
title, where it cannot be scrolled past.

Cards are read on a phone. Short lines, no tables, the action near the top.
"""
from __future__ import annotations

import html
from typing import Any, Optional

from . import schemas


def _esc(s: Any) -> str:
    return html.escape(str(s or ""))


def _clip(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def human_task(*, ref: str, title: str, why: str, how_md: str,
               where_url: Optional[str], verify_command: Optional[str],
               reply_schema: dict, expires_in: str) -> str:
    """The standard blocking-task card."""
    parts = [
        f"🔧 <b>{_esc(_clip(title, 120))}</b>",
        f"<code>{_esc(ref)}</code> · expires in {_esc(expires_in)}",
        "",
        f"<b>WHY</b>  {_esc(_clip(why, 600))}",
    ]
    if where_url:
        parts += ["", f"<b>WHERE</b>  {_esc(where_url)}"]
    if how_md:
        parts += ["", "<b>HOW</b>", f"<pre>{_esc(_clip(how_md, 2000))}</pre>"]
    parts += ["", schemas.describe(reply_schema)]
    if verify_command:
        parts += ["", f"<b>VERIFY</b>  <code>{_esc(verify_command)}</code>"]
    parts += ["", "Reply <code>SKIP &lt;reason&gt;</code> to release the block."]
    return "\n".join(parts)


def decision(*, ref: str, question: str, why: str, options: list[str],
             context: Optional[dict] = None, expires_in: str = "24h") -> str:
    """An approval gate. Outward-facing and irreversible actions land here."""
    parts = [
        f"🔀 <b>{_esc(_clip(question, 120))}</b>",
        f"<code>{_esc(ref)}</code> · expires in {_esc(expires_in)}",
        "",
        f"<b>WHY</b>  {_esc(_clip(why, 600))}",
    ]
    if context:
        parts += ["", "<b>CONTEXT</b>"]
        for k, v in list(context.items())[:10]:
            parts.append(f"  {_esc(k)}: <b>{_esc(_clip(str(v), 120))}</b>")
    parts += ["", "Reply with " + " / ".join(f"<b>{_esc(o)}</b>" for o in options)
              + " (or its number).",
              "Reply <code>SKIP &lt;reason&gt;</code> to decline and record why."]
    return "\n".join(parts)


def sintra(*, ref: str, why: str, bot: str, prompt: str,
           verify_command: str, expires_in: str = "24h") -> str:
    """The Sintra instruction card — architecture §7.

    Sintra is Cloudflare-blocked from this VPS. Pimlico's response was to keep
    calling it and publish the error text; on six consecutive days
    `"[Automation failed: Page.goto: Timeout 30000ms exceeded...]"` was posted to
    a live LinkedIn account. JPD's response is to stop pretending it is an API.

    The prompt below is generated from real dossier evidence, not a template —
    that is what makes pasting it worth the operator's time.
    """
    rule = "─" * 42
    return "\n".join([
        f"🤖 <b>SINTRA TASK</b>",
        f"<code>{_esc(ref)}</code> · expires in {_esc(expires_in)}",
        "",
        f"<b>WHY</b>  {_esc(_clip(why, 600))}",
        "",
        f"<b>WHERE</b>  https://sintra.ai → bot: <b>{_esc(bot)}</b> → new chat",
        "",
        "<b>PASTE THIS PROMPT VERBATIM</b>",
        f"<pre>{rule}\n{_esc(_clip(prompt, 2500))}\n{rule}</pre>",
        "",
        "<b>REPLY</b>  Paste Sintra's full output as a reply to THIS message.",
        "Reply <code>SKIP &lt;reason&gt;</code> to release the block and mark the step skipped.",
        "",
        f"<b>VERIFY</b>  <code>{_esc(verify_command)}</code>",
    ])


def rejected_reply(*, ref: str, error: str, attempt: int, max_attempts: int) -> str:
    """A failed parse RE-ASKS. It never persists a half-answer.

    Telling the operator exactly what was wrong is the whole point — "invalid
    reply" would send them back to guess.
    """
    return "\n".join([
        f"⚠️ <b>That reply could not be used</b> · <code>{_esc(ref)}</code>",
        "",
        _esc(error),
        "",
        f"<i>Attempt {attempt} of {max_attempts}. The task is still open — "
        f"reply again to this card.</i>",
    ])


def accepted_reply(*, ref: str, summary: str) -> str:
    return f"✅ <b>Accepted</b> · <code>{_esc(ref)}</code>\n{_esc(_clip(summary, 300))}"


def skipped(*, ref: str, reason: str) -> str:
    return (f"⏭️ <b>Skipped</b> · <code>{_esc(ref)}</code>\n"
            f"Recorded reason: {_esc(_clip(reason, 300))}\n"
            f"<i>The step is released and marked skipped — this is a decision, "
            f"not a failure.</i>")


def expired(*, ref: str, title: str, age_h: float) -> str:
    """An expired approval silently stalled a Pimlico build for five days.
    Expiry is announced, not merely recorded."""
    return "\n".join([
        f"⏰ <b>EXPIRED</b> · <code>{_esc(ref)}</code>",
        _esc(_clip(title, 120)),
        "",
        f"Open for {age_h:.0f}h with no reply. The run is still blocked.",
        "Reply to reopen it, or kill the run.",
    ])


def delivery(*, buyer: str, tiers: list[str], links: list[dict]) -> str:
    """Operator-visible copy of a buyer delivery, so a fulfilment is never silent."""
    lines = [f"💸 <b>Delivered</b> to {_esc(buyer)}",
             f"tiers: {', '.join(_esc(t) for t in tiers)}", ""]
    for l in links:
        # The download URL carries a bearer token. Operator-visible is fine —
        # this is a private topic — but it must never reach a log line.
        lines.append(f"  {_esc(l.get('tier'))}: expires {_esc(l.get('expires_at'))}")
    return "\n".join(lines)


def alert(*, title: str, detail: str, severity: str = "warning") -> str:
    icon = {"critical": "🔴", "warning": "🟠", "info": "🔵"}.get(severity, "🟠")
    return f"{icon} <b>{_esc(_clip(title, 120))}</b>\n{_esc(_clip(detail, 1000))}"
