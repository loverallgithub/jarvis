"""Telegram commands — the phone-side control surface.

SCOPE, AND WHY IT STOPS WHERE IT DOES
─────────────────────────────────────
Two tiers, and the boundary between them is the point:

  READ-ONLY   /status /tasks /connectors /discover /forge /sources /alerts
              /checkpoint /streams /help
  SAFE ACTION /skip /expire /harvest /verify

Nothing here spends LLM budget and nothing here rolls production. `forge run`
and `research run` cost real money per invocation, and `deploy` changes what is
serving; a Telegram group is a SHARED surface where a mistyped line, a forwarded
message, or one compromised account would be enough to trigger either. Those
stay on the box, where running them takes deliberate intent.

This is the same reasoning that keeps the dashboard read-only, and it is why the
split is enforced here in code rather than by convention: HANDLERS is the whole
command table, and there is no path from a chat message to anything not in it.

A command is answered in the topic it was sent from, so `/status` in #alerts
does not spray the answer across #decisions.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

import structlog

from .. import db

log = structlog.get_logger("console.commands")

MAX_REPLY = 3500          # Telegram hard-limits a message to 4096 characters.


def _clip(s: str, n: int = MAX_REPLY) -> str:
    return s if len(s) <= n else s[: n - 60] + "\n…truncated — full detail in /ui"


def _esc(v: Any) -> str:
    """HTML-escape for parse_mode=HTML. Telegram rejects a malformed entity, so
    an unescaped '<' in a task title would fail the whole send."""
    return (str("" if v is None else v).replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


# ── read-only ──────────────────────────────────────────────────────────────
async def cmd_status(_: list[str]) -> str:
    conns = await db.fetchrow(
        "SELECT count(*) AS total, count(*) FILTER (WHERE state='live') AS live "
        "  FROM connector_health")
    tasks_open = await db.fetchval(
        "SELECT count(*) FROM human_tasks WHERE status='open'") or 0
    arts = await db.fetchrow(
        "SELECT count(*) AS total, count(*) FILTER (WHERE offerable) AS offerable "
        "  FROM artifacts")
    streams = await db.fetchrow(
        "SELECT count(*) AS total, count(*) FILTER (WHERE thread_id IS NOT NULL) "
        "       AS configured FROM telegram_streams")
    claims = await db.fetchrow(
        "SELECT count(*) FILTER (WHERE supported IS FALSE) AS unsup, "
        "       count(*) AS total FROM claims "
        " WHERE id IN (SELECT claim_id FROM artifact_claims)")
    ck = await db.fetchrow(
        "SELECT id, label FROM checkpoints ORDER BY id DESC LIMIT 1")
    poll = await db.fetchrow(
        "SELECT extract(epoch FROM now() - last_success_at)::int AS age "
        "  FROM job_registry WHERE job_name='console.poll_replies'")
    needs = await db.fetchval("SELECT count(*) FROM needs") or 0
    signals = await db.fetchval("SELECT count(*) FROM signals") or 0

    age = (poll or {}).get("age")
    poll_txt = "alive" if age is not None and age < 180 else f"STALE ({age}s)"
    return (
        "📊 <b>JPD STATUS</b>\n"
        f"<code>connectors  {conns['live']}/{conns['total']} live</code>\n"
        f"<code>tasks       {tasks_open} open</code>\n"
        f"<code>streams     {streams['configured']}/{streams['total']} configured</code>\n"
        f"<code>artifacts   {arts['offerable']}/{arts['total']} offerable</code>\n"
        f"<code>claims      {claims['unsup']} unsupported of {claims['total']}</code>\n"
        f"<code>discovery   {signals} signals · {needs} needs</code>\n"
        f"<code>poller      {poll_txt}</code>\n"
        f"<code>checkpoint  #{(ck or {}).get('id')} {_esc((ck or {}).get('label'))}</code>")


async def cmd_tasks(_: list[str]) -> str:
    rows = await db.fetch(
        "SELECT ref, title, stream, "
        "       extract(epoch FROM now()-created_at)::int/3600 AS hrs "
        "  FROM human_tasks WHERE status='open' ORDER BY created_at LIMIT 20")
    if not rows:
        return "✅ <b>No open human tasks.</b> The queue is clear."
    out = [f"📋 <b>{len(rows)} open task(s)</b>"]
    out += [f"<code>{_esc(r['ref'])}</code> · {_esc(r['stream'])} · {r['hrs']}h\n"
            f"  {_esc(r['title'])}" for r in rows]
    out.append("\nReply to a card to answer it, or <code>/skip REF reason</code>.")
    return "\n".join(out)


async def cmd_connectors(_: list[str]) -> str:
    rows = await db.fetch(
        "SELECT connector, state, fail_streak FROM connector_health "
        " ORDER BY state DESC, connector")
    live = [r for r in rows if r["state"] == "live"]
    dead = [r for r in rows if r["state"] != "live"]
    out = [f"🔌 <b>{len(live)}/{len(rows)} live</b>", "",
           "<b>live</b>: " + ", ".join(_esc(r["connector"]) for r in live)]
    if dead:
        out += ["", "<b>not live</b>:"]
        out += [f"<code>{_esc(r['connector']):<22}</code> {_esc(r['state'])}"
                + (f" · {r['fail_streak']} fails" if r["fail_streak"] else "")
                for r in dead]
    return "\n".join(out)


async def cmd_discover(_: list[str]) -> str:
    sig = await db.fetchval("SELECT count(*) FROM signals") or 0
    clu = await db.fetchval("SELECT count(*) FROM clusters") or 0
    gates = await db.fetch(
        "SELECT gate, count(*) AS n, count(*) FILTER (WHERE passed) AS p "
        "  FROM gate_evaluations GROUP BY gate ORDER BY gate")
    needs = await db.fetch(
        "SELECT id, title, score, status FROM needs "
        " ORDER BY score DESC NULLS LAST LIMIT 8")
    out = [f"🔭 <b>DISCOVERY</b>\n<code>{sig} signals → {clu} clusters "
           f"→ {len(needs)} needs</code>", "", "<b>gates</b>"]
    out += [f"<code>{_esc(g['gate']):<16} {g['p']}/{g['n']}</code>" for g in gates]
    if needs:
        out += ["", "<b>needs</b>"]
        out += [f"<code>#{n['id']}</code> {_esc(n['title'])[:60]} "
                f"({_esc(n['status'])})" for n in needs]
    return "\n".join(out)


async def cmd_forge(_: list[str]) -> str:
    rows = await db.fetch(
        "SELECT id, need_id, tier, words, structural_ok, factual_ok, offerable "
        "  FROM artifacts ORDER BY need_id, id")
    if not rows:
        return "🔨 <b>No artifacts built.</b>"
    out = ["🔨 <b>ARTIFACTS</b>"]
    for r in rows:
        marks = ("✅" if r["structural_ok"] else "❌") + \
                ("✅" if r["factual_ok"] else "❌")
        sell = "OFFERABLE" if r["offerable"] else "withheld"
        out.append(f"<code>#{r['id']} {_esc(r['tier']):<13} {r['words']:>5}w</code> "
                   f"{marks} {sell}")
    bad = await db.fetch(
        "SELECT id, left(text,70) AS t FROM claims WHERE supported IS FALSE "
        "   AND id IN (SELECT claim_id FROM artifact_claims) ORDER BY id")
    if bad:
        out += ["", f"<b>{len(bad)} unsupported claim(s)</b> — this is what "
                    "withholds them:"]
        out += [f"<code>{c['id']}</code> {_esc(c['t'])}…" for c in bad]
    out.append("\n<i>structural·factual — both must pass to sell.</i>")
    return "\n".join(out)


async def cmd_sources(_: list[str]) -> str:
    rows = await db.fetch(
        "SELECT name, health_state, source_type, fail_streak FROM sources "
        " ORDER BY health_state DESC, name")
    out = ["📡 <b>SOURCES</b>"]
    out += [f"<code>{_esc(r['name']):<22}</code> {_esc(r['health_state'])} "
            f"· {_esc(r['source_type'])}" for r in rows]
    return "\n".join(out)


async def cmd_streams(_: list[str]) -> str:
    rows = await db.fetch(
        "SELECT stream, chat_id, thread_id FROM telegram_streams ORDER BY stream")
    ok = sum(1 for r in rows if r["thread_id"] is not None)
    out = [f"💬 <b>{ok}/{len(rows)} streams configured</b>"]
    out += [f"<code>{_esc(r['stream']):<12} thread {r['thread_id']}</code>"
            for r in rows]
    return "\n".join(out)


async def cmd_checkpoint(_: list[str]) -> str:
    r = await db.fetchrow(
        "SELECT id, label, phase, resumable_from, reason, created_at "
        "  FROM checkpoints ORDER BY id DESC LIMIT 1")
    if not r:
        return "No checkpoint written yet."
    return (f"🧭 <b>CHECKPOINT #{r['id']}</b> <code>{_esc(r['label'])}</code>\n"
            f"phase {_esc(r['phase'])} · {r['created_at']:%Y-%m-%d %H:%M} UTC\n"
            f"resumable from <code>{_esc(r['resumable_from'] or '—')}</code>\n\n"
            f"{_esc(r['reason'])[:1800]}…")


async def cmd_alerts(_: list[str]) -> str:
    rows = await db.fetch(
        "SELECT connector, state, fail_streak FROM connector_health "
        " WHERE fail_streak > 0 ORDER BY fail_streak DESC LIMIT 15")
    if not rows:
        return "🟢 <b>Nothing degrading.</b> No connector has a failure streak."
    out = ["🚨 <b>FAILING</b>"]
    out += [f"<code>{_esc(r['connector']):<22}</code> {r['fail_streak']} fails "
            f"· {_esc(r['state'])}" for r in rows]
    return "\n".join(out)


# ── safe actions — no spend, no deploy ─────────────────────────────────────
async def cmd_skip(args: list[str]) -> str:
    """Routed through `apply_reply`, NOT a direct UPDATE.

    `SKIP <reason>` is already a first-class reply the schema understands, so
    going through the normal reply path means a /skip is recorded, audited and
    re-asked on a bad parse exactly like a typed reply. A direct write here
    would be a second way to resolve a task that skips all of that.
    """
    if len(args) < 2:
        return ("usage: <code>/skip REF reason</code>\n"
                "A reason is mandatory — \"skipped\" with no why is unusable later.")
    ref, reason = args[0].upper(), " ".join(args[1:])
    from . import tasks as tasks_mod
    row = await db.fetchrow(
        "SELECT id FROM human_tasks WHERE upper(ref)=$1 AND status='open'", ref)
    if not row:
        return f"No OPEN task <code>{_esc(ref)}</code>."
    parsed = await tasks_mod.apply_reply(int(row["id"]), f"SKIP {reason}")
    if not parsed.ok:
        return f"❌ rejected: {_esc(parsed.error)}"
    return f"⏭ <code>{_esc(ref)}</code> skipped — {_esc(reason)}"


async def cmd_expire(_: list[str]) -> str:
    from . import tasks as tasks_mod
    rows = await tasks_mod.expire_due()
    if not rows:
        return "🕒 Nothing overdue."
    refs = ", ".join(f"<code>{_esc(r.get('ref'))}</code>" for r in rows)
    return f"🕒 Expired {len(rows)} overdue task(s): {refs}"


async def cmd_harvest(args: list[str]) -> str:
    """One connector, or all of them. No LLM spend — this is network I/O only."""
    from ..connectors import health
    try:
        if args:
            r = await health.harvest(args[0])
            return (f"🌾 <code>{_esc(args[0])}</code> → "
                    f"{_esc(getattr(r, 'recorded', r))} recorded")
        out = await health.harvest_all()
        total = sum(out.values())
        top = sorted(out.items(), key=lambda kv: -kv[1])[:8]
        body = "\n".join(f"<code>{_esc(k):<22}</code> {v}" for k, v in top)
        return f"🌾 <b>{total} signals</b> across {len(out)} connectors\n{body}"
    except Exception as e:                                       # noqa: BLE001
        return (f"❌ harvest failed: {_esc(type(e).__name__)}: "
                f"{_esc(str(e)[:200])}")


async def cmd_verify(_: list[str]) -> str:
    r = await db.fetchrow(
        "SELECT step_id, status, accepted, acceptance_reason FROM steps "
        " ORDER BY id DESC LIMIT 1")
    if not r:
        return "No steps have run."
    mark = "✅" if r["accepted"] else "❌"
    return (f"{mark} last step <code>{_esc(r['step_id'])}</code> — "
            f"{_esc(r['status'])}\n{_esc(r['acceptance_reason'] or '')}")


async def cmd_help(_: list[str]) -> str:
    return (
        "🤖 <b>JPD COMMANDS</b>\n\n"
        "<b>read-only</b>\n"
        "<code>/status</code> the dashboard in one card\n"
        "<code>/tasks</code> open human-task queue\n"
        "<code>/forge</code> artifacts + what withholds them\n"
        "<code>/discover</code> funnel, gates, needs\n"
        "<code>/connectors</code> live vs dormant\n"
        "<code>/sources</code> source health\n"
        "<code>/streams</code> telegram stream config\n"
        "<code>/alerts</code> anything with a failure streak\n"
        "<code>/checkpoint</code> latest checkpoint\n\n"
        "<b>safe actions</b> — no spend, no deploy\n"
        "<code>/skip REF reason</code> release a blocked task\n"
        "<code>/expire</code> expire overdue tasks\n"
        "<code>/harvest name</code> re-run one connector\n"
        "<code>/verify</code> last step's acceptance\n\n"
        "<i>Spending and deploys are deliberately NOT here — they stay on the "
        "box. Full detail lives on the dashboard.</i>")


HANDLERS: dict[str, Callable[[list[str]], Any]] = {
    "status": cmd_status, "tasks": cmd_tasks, "connectors": cmd_connectors,
    "discover": cmd_discover, "forge": cmd_forge, "sources": cmd_sources,
    "streams": cmd_streams, "checkpoint": cmd_checkpoint, "alerts": cmd_alerts,
    "skip": cmd_skip, "expire": cmd_expire, "harvest": cmd_harvest,
    "verify": cmd_verify, "help": cmd_help, "start": cmd_help,
}


def parse(text: str) -> Optional[tuple[str, list[str]]]:
    """`/forge@jpd_com_bot 13` → ('forge', ['13']). None if not a command.

    The `@botname` suffix is stripped: Telegram appends it automatically in
    groups, and without this every command would 404 in exactly the place the
    commands are meant to be used.
    """
    t = (text or "").strip()
    if not t.startswith("/"):
        return None
    parts = t[1:].split()
    if not parts:
        return None
    name = parts[0].split("@", 1)[0].lower()
    return (name, parts[1:]) if name else None


async def dispatch(text: str) -> Optional[str]:
    """Returns the reply, or None if this is not a command at all.

    An unknown /command DOES get an answer — silence there is indistinguishable
    from a dead bot, which is the failure this whole surface exists to avoid.
    """
    parsed = parse(text)
    if parsed is None:
        return None
    name, args = parsed
    fn = HANDLERS.get(name)
    if fn is None:
        return f"Unknown command <code>/{_esc(name)}</code>. Try <code>/help</code>."
    try:
        return _clip(await fn(args))
    except Exception as e:                                       # noqa: BLE001
        log.warning("commands.failed", command=name, error=str(e)[:200])
        return f"❌ <code>/{_esc(name)}</code> failed: {_esc(type(e).__name__)}"
