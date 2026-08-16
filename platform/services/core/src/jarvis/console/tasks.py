"""Human-task lifecycle.

────────────────────────────────────────────────────────────────────────────
THE INVARIANT: A BLOCKED RUN IS VISIBLE EVEN IF TELEGRAM IS DOWN
────────────────────────────────────────────────────────────────────────────
The task row is written **first**, and posting the card is a separate, retryable
act. If Telegram is dormant (HT-001 outstanding, token missing, API down), the
task still exists, the run is still visibly `blocked_on_human`, and `jpd resume`
still shows it. Only the notification is missing, and that missing-ness is
itself recorded.

The alternative — post first, then record — means an outage makes work vanish.
That is the C7 failure Pimlico demonstrated in the other direction: a hermes
roll blinded the operator for ~90s and a real outage blinded them entirely.

Tasks are idempotent on `idempotency_key`. A step that blocks, resumes, and
blocks again must find its EXISTING card, not post a second one — otherwise
every resume attempt spams the topic and the operator cannot tell which card is
live.
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog

from .. import db
from . import cards, schemas
from .schemas import ParsedReply
from .telegram import StreamNotConfigured, TelegramClient, TelegramError

log = structlog.get_logger("console.tasks")

DEFAULT_TTL_HOURS = 24
MAX_REPLY_ATTEMPTS = 5


@dataclass
class Task:
    id: int
    ref: str
    status: str
    type: str
    reply_json: Optional[dict] = None
    skip_reason: Optional[str] = None

    @property
    def resolved(self) -> bool:
        return self.status in ("replied", "skipped")


def new_ref(prefix: str = "JPD") -> str:
    return f"{prefix}-{secrets.token_hex(3).upper()}"


def _fmt_expires(expires_at: datetime) -> str:
    delta = expires_at - datetime.now(timezone.utc)
    hours = max(0, int(delta.total_seconds() // 3600))
    return f"{hours}h" if hours < 48 else f"{hours // 24}d"


async def create(*, type: str, title: str, why: str, how_md: str = "",
                 reply_schema: dict, stream: str = "human-tasks",
                 where_url: Optional[str] = None,
                 verify_command: Optional[str] = None,
                 options: Optional[list[str]] = None,
                 run_id: Optional[int] = None, step_id: Optional[str] = None,
                 idempotency_key: Optional[str] = None,
                 ttl_hours: int = DEFAULT_TTL_HOURS,
                 card_text: Optional[str] = None,
                 ref: Optional[str] = None) -> Task:
    """Create (or find) a human task, then try to post its card.

    ⚠️ `ref` is accepted, not always generated. Callers that pre-render a card
    (the Sintra and decision builders) must pass the SAME ref they printed on
    it — otherwise the card says `jpd tasks show SIN-ABC123` and the row is
    `JPD-F0DC60`, so the verify command the operator was told to run finds
    nothing. Caught by a test; it would have looked like a missing task.
    """
    if not why.strip():
        # NOT NULL in the schema, and enforced here with a message that says
        # why: a task with no stated consequence gets skipped for weeks.
        raise ValueError(
            "a human task must state WHY it is blocking and what it costs — "
            "Pimlico's consequence-free prompts were ignored for weeks")

    if idempotency_key:
        existing = await db.fetchrow(
            "SELECT id, ref, status, type, reply_json, skip_reason FROM human_tasks "
            "WHERE idempotency_key = $1", idempotency_key)
        if existing:
            return _row_to_task(existing)

    ref = ref or new_ref()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)

    tid = await db.fetchval(
        """
        INSERT INTO human_tasks (run_id, step_id, ref, type, title, why, how_md,
                                 where_url, verify_command, reply_schema, options,
                                 stream, status, expires_at, idempotency_key)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11::jsonb,$12,'open',$13,$14)
        RETURNING id
        """,
        run_id, step_id, ref, type, title[:300], why, how_md,
        where_url, verify_command, json.dumps(reply_schema),
        json.dumps(options) if options else None, stream, expires_at,
        idempotency_key)

    log.info("tasks.created", ref=ref, type=type, run_id=run_id, step_id=step_id)

    text = card_text or cards.human_task(
        ref=ref, title=title, why=why, how_md=how_md, where_url=where_url,
        verify_command=verify_command, reply_schema=reply_schema,
        expires_in=_fmt_expires(expires_at))
    await _try_post(int(tid), ref, stream, text)

    return Task(id=int(tid), ref=ref, status="open", type=type)


async def _try_post(task_id: int, ref: str, stream: str, text: str) -> bool:
    """Post the card. Failure is recorded, never fatal.

    A task that exists but could not be announced is a strictly better outcome
    than a task that was announced but not recorded — the first is recoverable
    by a retry, the second is lost work.
    """
    client = TelegramClient()
    try:
        msg = await client.post(stream, text)
    except (StreamNotConfigured, TelegramError) as e:
        log.warning("tasks.post_failed", ref=ref, stream=stream, error=str(e)[:200],
                    hint="the task EXISTS and the run is visibly blocked; only the "
                         "notification is missing. It will be retried.")
        await db.execute(
            "UPDATE human_tasks SET last_parse_error = $2 WHERE id = $1",
            task_id, f"card not posted: {str(e)[:200]}")
        return False

    await db.execute(
        "UPDATE human_tasks SET telegram_message_id=$2, telegram_thread_id=$3, "
        "assigned_channel='telegram', last_parse_error=NULL WHERE id=$1",
        task_id, msg.message_id, msg.thread_id)
    return True


async def post_pending() -> int:
    """Retry cards for open tasks that were never announced.

    Runs when telegram comes back. Without it, everything created during an
    outage stays silently queued — which is the failure this module is designed
    to prevent, arriving one step later.
    """
    rows = await db.fetch(
        "SELECT id, ref, stream, title, why, how_md, where_url, verify_command, "
        "reply_schema, expires_at, type FROM human_tasks "
        "WHERE status='open' AND telegram_message_id IS NULL ORDER BY created_at")
    posted = 0
    for r in rows:
        schema = r["reply_schema"]
        if isinstance(schema, str):
            schema = json.loads(schema)
        text = cards.human_task(
            ref=r["ref"], title=r["title"], why=r["why"], how_md=r["how_md"] or "",
            where_url=r["where_url"], verify_command=r["verify_command"],
            reply_schema=schema, expires_in=_fmt_expires(r["expires_at"]))
        if await _try_post(r["id"], r["ref"], r["stream"] or "human-tasks", text):
            posted += 1
    if posted:
        log.info("tasks.backlog_posted", posted=posted, of=len(rows))
    return posted


def _row_to_task(row) -> Task:
    reply = row["reply_json"]
    if isinstance(reply, str):
        reply = json.loads(reply)
    return Task(id=int(row["id"]), ref=row["ref"], status=row["status"],
                type=row["type"], reply_json=reply,
                skip_reason=row.get("skip_reason") if isinstance(row, dict)
                else row["skip_reason"])


async def get(ref: str) -> Optional[Task]:
    row = await db.fetchrow(
        "SELECT id, ref, status, type, reply_json, skip_reason FROM human_tasks "
        "WHERE ref = $1", ref)
    return _row_to_task(row) if row else None


async def by_idempotency(key: str) -> Optional[Task]:
    row = await db.fetchrow(
        "SELECT id, ref, status, type, reply_json, skip_reason FROM human_tasks "
        "WHERE idempotency_key = $1", key)
    return _row_to_task(row) if row else None


# ---------------------------------------------------------------------------
# replies
# ---------------------------------------------------------------------------

async def apply_reply(task_id: int, text: str) -> ParsedReply:
    """Validate a reply against the task's schema and resolve it, or re-ask.

    🔴 A failed parse does NOT persist. The task stays open, the operator is
    told exactly what was wrong, and they reply again. Storing a half-answer
    would make "answered badly" and "answered well" the same shape — which is
    how Pimlico's free-text prompts became unusable.
    """
    row = await db.fetchrow(
        "SELECT id, ref, status, reply_schema, reply_attempts, stream, "
        "telegram_message_id, run_id, step_id FROM human_tasks WHERE id = $1", task_id)
    if row is None:
        return ParsedReply(ok=False, error="no such task")
    if row["status"] != "open":
        return ParsedReply(ok=False, error=f"task is already {row['status']}")

    schema = row["reply_schema"]
    if isinstance(schema, str):
        schema = json.loads(schema)

    parsed = schemas.validate(schema, text)
    client = TelegramClient()

    if not parsed.ok:
        attempts = int(row["reply_attempts"]) + 1
        await db.execute(
            "UPDATE human_tasks SET reply_attempts=$2, last_parse_error=$3 WHERE id=$1",
            task_id, attempts, (parsed.error or "")[:500])
        log.info("tasks.reply_rejected", ref=row["ref"], attempt=attempts,
                 error=(parsed.error or "")[:120])
        if attempts <= MAX_REPLY_ATTEMPTS:
            await _safe_reply(client, row, cards.rejected_reply(
                ref=row["ref"], error=parsed.error or "invalid",
                attempt=attempts, max_attempts=MAX_REPLY_ATTEMPTS))
        return parsed

    if parsed.skipped:
        await db.execute(
            "UPDATE human_tasks SET status='skipped', skip_reason=$2, "
            "reply_json=$3::jsonb, resolved_at=now() WHERE id=$1",
            task_id, parsed.skip_reason, json.dumps(parsed.value))
        log.info("tasks.skipped", ref=row["ref"], reason=(parsed.skip_reason or "")[:120])
        await _safe_reply(client, row, cards.skipped(
            ref=row["ref"], reason=parsed.skip_reason or ""))
        return parsed

    await db.execute(
        "UPDATE human_tasks SET status='replied', reply_json=$2::jsonb, "
        "resolved_at=now(), last_parse_error=NULL WHERE id=$1",
        task_id, json.dumps(parsed.value))
    log.info("tasks.replied", ref=row["ref"], run_id=row["run_id"], step_id=row["step_id"])

    summary = next((str(v) for v in (parsed.value or {}).values()), "")
    await _safe_reply(client, row, cards.accepted_reply(
        ref=row["ref"], summary=summary[:200]))
    return parsed


async def _safe_reply(client: TelegramClient, row, text: str) -> None:
    """Acknowledgements must never break the lifecycle.

    The task's state is already committed by the time we get here. Failing to
    say "accepted" is cosmetic; raising would roll a resolved task back to
    looking unresolved.
    """
    if not row["telegram_message_id"]:
        return
    try:
        await client.reply_to(row["stream"] or "human-tasks",
                              int(row["telegram_message_id"]), text)
    except Exception as e:                                       # noqa: BLE001
        log.warning("tasks.ack_failed", ref=row["ref"], error=str(e)[:150])


# ---------------------------------------------------------------------------
# expiry
# ---------------------------------------------------------------------------

async def expire_due() -> list[dict]:
    """Announce expiry; do not silently drop.

    An expired approval stalled a Pimlico build for five days and nothing said
    so. The status changes AND a card is posted, because the run stays blocked
    either way — the operator needs to know it is on them.
    """
    rows = await db.fetch(
        "SELECT id, ref, title, stream, telegram_message_id, created_at "
        "FROM human_tasks WHERE status='open' AND expires_at < now()")
    if not rows:
        # A sweep that found nothing to expire still RAN. Recording success
        # only on the expiry path left last_success_at NULL forever on the
        # normal path, so the job read as never-run and its staleness signal
        # was unusable.
        await db.execute(
            "UPDATE job_registry SET last_success_at = now() WHERE job_name = $1",
            "console.expire_tasks")
        return []

    client = TelegramClient()
    out = []
    for r in rows:
        await db.execute("UPDATE human_tasks SET status='expired' WHERE id=$1", r["id"])
        age_h = (datetime.now(timezone.utc) - r["created_at"]).total_seconds() / 3600
        log.warning("tasks.expired", ref=r["ref"], age_hours=round(age_h, 1))
        try:
            await client.post(r["stream"] or "human-tasks",
                              cards.expired(ref=r["ref"], title=r["title"], age_h=age_h))
        except Exception as e:                                   # noqa: BLE001
            log.warning("tasks.expiry_notice_failed", ref=r["ref"], error=str(e)[:150])
        out.append({"ref": r["ref"], "title": r["title"], "age_hours": round(age_h, 1)})

    await db.execute(
        "UPDATE job_registry SET last_success_at = now() WHERE job_name = $1",
        "console.expire_tasks")
    return out


async def open_tasks() -> list[dict]:
    rows = await db.fetch(
        """
        SELECT ref, type, title, why, verify_command, stream, status, created_at,
               expires_at, run_id, step_id, reply_attempts, last_parse_error,
               (telegram_message_id IS NULL) AS unannounced,
               (expires_at < now()) AS overdue
          FROM human_tasks WHERE status = 'open' ORDER BY created_at
        """)
    return [dict(r) for r in rows]


async def reopen(ref: str, ttl_hours: int = DEFAULT_TTL_HOURS) -> bool:
    ok = await db.fetchval(
        "UPDATE human_tasks SET status='open', expires_at=now() + make_interval(hours => $2) "
        "WHERE ref=$1 AND status IN ('expired','open') RETURNING id", ref, ttl_hours)
    return ok is not None
