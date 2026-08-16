"""Telegram client — one topic per stream.

Pimlico's client posts everything into a single `chat_id` with no
`message_thread_id`: briefs, alerts, approvals and errors in one undifferentiated
feed. An approval that stalled a €297 build sat between a metrics dump and a
Reddit drip log and was missed for five days.

Here every post names its **stream**, and the `(chat_id, thread_id)` pair comes
from the `telegram_streams` registry. A stream with no ids configured **cannot
be posted to** — it raises rather than falling back to the General topic, because
a message in the wrong place is worse than a message that failed loudly.

🔴 The bot token is never logged, never echoed, never returned. It goes in the
URL path (Telegram's design), so **no function here may log a full request URL.**
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

import httpx
import structlog

from .. import db
from ..connectors.base import ProbeResult, TestResult

log = structlog.get_logger("console.telegram")

API_BASE = "https://api.telegram.org"
STREAMS = ("decisions", "human-tasks", "sintra", "discoveries", "revenue", "alerts")


class StreamNotConfigured(RuntimeError):
    """The stream has no chat_id/thread_id. HT-001 is outstanding.

    Raised rather than defaulted. Posting to General instead is precisely the
    single-stream failure this module exists to eliminate.
    """


class TelegramError(RuntimeError):
    """Telegram rejected the call. Always raised, never returned as a string."""


@dataclass(frozen=True)
class PostedMessage:
    message_id: int
    chat_id: int
    thread_id: Optional[int]


class TelegramClient:
    name = "telegram"
    kind = "api"

    def __init__(self, token: str = ""):
        self._token = token or os.environ.get("JPD_TELEGRAM_BOT_TOKEN", "")

    @property
    def configured(self) -> bool:
        return bool(self._token) and self._token != "CHANGE_ME"

    def _url(self, method: str) -> str:
        return f"{API_BASE}/bot{self._token}/{method}"

    async def _call(self, method: str, payload: dict[str, Any]) -> dict:
        if not self.configured:
            raise TelegramError("JPD_TELEGRAM_BOT_TOKEN absent or CHANGE_ME")
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(self._url(method), json=payload)
        try:
            body = r.json()
        except Exception:                                        # noqa: BLE001
            raise TelegramError(f"{method} -> {r.status_code}, non-JSON body") from None
        if not body.get("ok"):
            # Log the METHOD and the description — never the url, which carries
            # the token.
            desc = str(body.get("description", ""))[:200]
            log.warning("telegram.api_error", method=method, status=r.status_code,
                        description=desc)
            raise TelegramError(f"{method}: {desc or r.status_code}")
        return body.get("result") or {}

    # -- stream registry ---------------------------------------------------
    @staticmethod
    async def resolve(stream: str) -> tuple[int, Optional[int]]:
        if stream not in STREAMS:
            raise StreamNotConfigured(f"unknown stream {stream!r}; known: {STREAMS}")
        row = await db.fetchrow(
            "SELECT chat_id, thread_id, enabled FROM telegram_streams WHERE stream = $1",
            stream)
        if row is None or row["chat_id"] is None:
            raise StreamNotConfigured(
                f"stream {stream!r} has no chat_id — HT-001 (create the Telegram forum) "
                f"is outstanding. Refusing to post to the default topic.")
        if not row["enabled"]:
            raise StreamNotConfigured(f"stream {stream!r} is disabled")
        return int(row["chat_id"]), (int(row["thread_id"]) if row["thread_id"] is not None else None)

    # -- posting -----------------------------------------------------------
    async def post(self, stream: Optional[str], text: str, *,
                   reply_markup: Optional[dict] = None,
                   chat_id: Optional[int] = None,
                   thread_id: Optional[int] = None) -> PostedMessage:
        """Post to a named stream, or to an EXPLICIT (chat_id, thread_id).

        The explicit form exists for command replies, which must land in the
        topic the command was typed into — that topic is known from the update
        and is not necessarily one of the six registered streams. Passing a
        stream name still resolves through the registry exactly as before, so
        every existing caller is unaffected.
        """
        if stream is not None:
            chat_id, thread_id = await self.resolve(stream)
        elif chat_id is None:
            raise StreamNotConfigured(
                "post() needs either a stream name or an explicit chat_id")
        payload: dict[str, Any] = {
            "chat_id": chat_id, "text": text[:4096],
            "parse_mode": "HTML", "disable_web_page_preview": True,
        }
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        if reply_markup:
            payload["reply_markup"] = reply_markup

        res = await self._call("sendMessage", payload)
        mid = int(res.get("message_id", 0))
        log.info("telegram.posted", stream=stream or f"thread:{thread_id}",
                 message_id=mid, chars=len(text))
        return PostedMessage(message_id=mid, chat_id=int(chat_id),
                             thread_id=thread_id)

    async def reply_to(self, stream: str, message_id: int, text: str) -> PostedMessage:
        chat_id, thread_id = await self.resolve(stream)
        payload: dict[str, Any] = {
            "chat_id": chat_id, "text": text[:4096], "parse_mode": "HTML",
            "reply_to_message_id": message_id, "disable_web_page_preview": True,
        }
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        res = await self._call("sendMessage", payload)
        return PostedMessage(int(res.get("message_id", 0)), chat_id, thread_id)

    async def get_updates(self, offset: int, timeout_s: int = 25) -> list[dict]:
        """Long-poll. `offset` is the clamped watermark + 1."""
        return list(await self._call("getUpdates", {
            "offset": offset, "timeout": timeout_s,
            "allowed_updates": ["message"],
        }) or [])

    # -- connector contract ------------------------------------------------
    async def probe(self) -> ProbeResult:
        if not self.configured:
            return ProbeResult(ok=False, detail="bot token absent or CHANGE_ME")
        try:
            me = await self._call("getMe", {})
            return ProbeResult(ok=True, detail=f"getMe ok: @{me.get('username','?')}")
        except Exception as e:                                   # noqa: BLE001
            return ProbeResult(ok=False, detail=str(e)[:200])

    async def contract_test(self) -> TestResult:
        """Reachable is not enough — every stream must resolve to real ids.

        A bot that authenticates but has no topic ids will post nothing
        anywhere, which is indistinguishable from silence.
        """
        if not self.configured:
            return TestResult(ok=False, detail="bot token absent or CHANGE_ME")
        try:
            me = await self._call("getMe", {})
        except Exception as e:                                   # noqa: BLE001
            return TestResult(ok=False, detail=str(e)[:200])

        rows = await db.fetch(
            "SELECT stream, chat_id, thread_id FROM telegram_streams WHERE enabled")
        missing = [r["stream"] for r in rows if r["chat_id"] is None]
        if missing:
            return TestResult(
                ok=False,
                detail=f"HT-001 outstanding — no chat_id for: {', '.join(sorted(missing))}",
                observed_shape={"bot": me.get("username")})

        no_thread = [r["stream"] for r in rows if r["thread_id"] is None]
        if no_thread:
            return TestResult(
                ok=False,
                detail=f"no thread_id for: {', '.join(sorted(no_thread))} — posts would "
                       f"land in General, which is the exact problem topics solve")
        return TestResult(ok=True,
                          detail=f"@{me.get('username')} with {len(rows)} streams configured",
                          observed_shape={"streams": len(rows)})


async def configure_stream(stream: str, chat_id: int, thread_id: Optional[int]) -> None:
    await db.execute(
        "UPDATE telegram_streams SET chat_id=$2, thread_id=$3, updated_at=now() "
        "WHERE stream=$1", stream, chat_id, thread_id)
    log.info("telegram.stream_configured", stream=stream, thread_id=thread_id)


async def stream_status() -> list[dict]:
    rows = await db.fetch(
        "SELECT stream, chat_id, thread_id, enabled, purpose FROM telegram_streams "
        "ORDER BY stream")
    return [dict(r) for r in rows]
