"""`kind="human"` as a first-class connector.

This is the generalisation of the Sintra bridge, and it is what lets JPD use
the ~two-thirds of the owned product portfolio that has no API at all. An
architecture that can only consume APIs can use a third of what has been paid
for.

────────────────────────────────────────────────────────────────────────────
HOW A STEP BLOCKS AND RESUMES
────────────────────────────────────────────────────────────────────────────
`request()` is **idempotent on `key`** and returns one of three states:

    blocked  → no task existed, or it exists and is unanswered.
               The step returns StepResult.blocked(...) and the engine records
               `blocked_on_human`. The run stops, visibly.

    replied  → the operator answered and it parsed. The step gets the TYPED
               value and carries on as if the call had returned normally.

    skipped  → the operator explicitly declined, with a recorded reason. Not a
               failure, not a timeout — a decision.

So resuming is just running the step again. The second run finds the resolved
task and proceeds. There is no separate resume path to keep in sync, and no
callback that can be lost.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional

import structlog

from . import cards, tasks
from .tasks import Task

log = structlog.get_logger("console.human")

State = Literal["blocked", "replied", "skipped"]


@dataclass(frozen=True)
class HumanResponse:
    state: State
    ref: str
    value: Optional[dict[str, Any]] = None
    reason: Optional[str] = None

    @property
    def blocked(self) -> bool:
        return self.state == "blocked"

    @property
    def text(self) -> str:
        """Convenience for `text` schemas. Empty when not answered — callers
        must check `state` first, and the acceptance predicate will catch them
        if they do not."""
        return (self.value or {}).get("text", "")


def _from_task(t: Task) -> HumanResponse:
    if t.status == "replied":
        return HumanResponse("replied", t.ref, value=t.reply_json)
    if t.status == "skipped":
        return HumanResponse("skipped", t.ref, reason=t.skip_reason,
                             value=t.reply_json)
    # open OR expired. An expired task still blocks — the work is not done, and
    # silently proceeding would be the worst possible reading of a deadline.
    return HumanResponse("blocked", t.ref)


async def request(*, key: str, title: str, why: str, reply_schema: dict,
                  how_md: str = "", where_url: Optional[str] = None,
                  verify_command: Optional[str] = None,
                  type: str = "task", stream: str = "human-tasks",
                  options: Optional[list[str]] = None,
                  run_id: Optional[int] = None, step_id: Optional[str] = None,
                  ttl_hours: int = tasks.DEFAULT_TTL_HOURS,
                  card_text: Optional[str] = None) -> HumanResponse:
    existing = await tasks.by_idempotency(key)
    if existing is not None:
        return _from_task(existing)

    t = await tasks.create(
        type=type, title=title, why=why, how_md=how_md, reply_schema=reply_schema,
        stream=stream, where_url=where_url, verify_command=verify_command,
        options=options, run_id=run_id, step_id=step_id, idempotency_key=key,
        ttl_hours=ttl_hours, card_text=card_text)
    log.info("human.blocked", ref=t.ref, key=key, step_id=step_id, run_id=run_id)
    return HumanResponse("blocked", t.ref)


async def decide(*, key: str, question: str, why: str, options: list[str],
                 context: Optional[dict] = None, run_id: Optional[int] = None,
                 step_id: Optional[str] = None,
                 ttl_hours: int = tasks.DEFAULT_TTL_HOURS) -> HumanResponse:
    """An approval gate.

    Outward-facing and irreversible actions default to a human decision — the
    publish gate, the price gate, cold outreach. Pimlico fires Success.ai
    unconditionally by explicit prior instruction; JPD re-gates it by default
    and ungating is a deliberate config change.
    """
    if len(options) < 2:
        raise ValueError("a decision needs at least two options")

    existing = await tasks.by_idempotency(key)
    if existing is not None:
        return _from_task(existing)

    ref = tasks.new_ref("DEC")
    text = cards.decision(ref=ref, question=question, why=why, options=options,
                          context=context, expires_in=f"{ttl_hours}h")
    t = await tasks.create(
        type="decision", title=question, why=why,
        reply_schema={"type": "choice", "options": options},
        stream="decisions", options=options, run_id=run_id, step_id=step_id,
        idempotency_key=key, ttl_hours=ttl_hours, card_text=text, ref=ref)
    return HumanResponse("blocked", t.ref)


async def sintra(*, key: str, why: str, bot: str, prompt: str,
                 min_chars: int = 200, run_id: Optional[int] = None,
                 step_id: Optional[str] = None,
                 ttl_hours: int = tasks.DEFAULT_TTL_HOURS) -> HumanResponse:
    """The Sintra bridge — architecture §7.

    Sintra is Cloudflare-blocked from this VPS, verified failing every day since
    ≈2026-07-25. Pimlico kept calling it, caught the exception, returned the
    error text as an ordinary string, and published it to a live LinkedIn
    account on six consecutive days.

    Here Sintra is a HUMAN connector. Three properties matter:
      · the prompt is generated from real dossier evidence, not a template
      · the reply is parsed against a schema — a failed parse re-asks
      · nothing Sintra-shaped can auto-publish, because the text schema rejects
        failure markers before the value is ever returned
    """
    existing = await tasks.by_idempotency(key)
    if existing is not None:
        return _from_task(existing)

    ref = tasks.new_ref("SIN")
    verify = f"jpd tasks show {ref}"
    text = cards.sintra(ref=ref, why=why, bot=bot, prompt=prompt,
                        verify_command=verify, expires_in=f"{ttl_hours}h")
    t = await tasks.create(
        type="sintra", title=f"Sintra · {bot}", why=why,
        how_md=prompt, reply_schema={"type": "text", "min_chars": min_chars},
        stream="sintra", verify_command=verify, run_id=run_id, step_id=step_id,
        idempotency_key=key, ttl_hours=ttl_hours, card_text=text, ref=ref)
    log.info("human.sintra_requested", ref=t.ref, bot=bot, step_id=step_id)
    return HumanResponse("blocked", t.ref)
