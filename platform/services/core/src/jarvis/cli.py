"""The ``jpd`` CLI — the operator's hands on the runtime.

Design rule carried from Pimlico: **every command that reports state must be
runnable when there is no state.** Pimlico's status tooling assumed rows
existed and threw when they did not, which meant the tooling was least usable
exactly when you most needed it — at the start, and after a wipe.

`jpd resume` against an empty database prints an empty report and exits 0.
That is the phase-0 exit criterion, and it is a real requirement, not a
formality.

Exit codes
    0  success, including "nothing to resume"
    1  a real error (bad args, DB unreachable, migration drift)
    2  `verify --last` found the stored verdict no longer holds
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db
from .config import settings, credential_status
from .connectors import base as connectors
from .runtime import checkpoints, engine, lease as lease_mod, registry

CHECKPOINT_MD = Path(os.environ.get("JPD_CHECKPOINT_MD", "/opt/jarvis/checkpoints/CHECKPOINT.md"))

# --- tiny output helpers ---------------------------------------------------

BOLD, DIM, RED, GRN, YEL, RST = "\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m"
if not sys.stdout.isatty():
    BOLD = DIM = RED = GRN = YEL = RST = ""


def _p(msg: str = "") -> None:
    print(msg)


def _json_default(o: Any) -> str:
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


def _dump(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=_json_default))


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

async def cmd_db_migrate(args) -> int:
    report = await db.migrate(dry_run=args.dry_run)
    if not report:
        _p(f"{YEL}no migration files found in {db.MIGRATIONS_DIR}{RST}")
        return 1
    for r in report:
        mark = {"applied": f"{GRN}applied{RST}",
                "already_applied": f"{DIM}already applied{RST}",
                "would_apply": f"{YEL}would apply{RST}"}[r["action"]]
        _p(f"  {r['version']:<24} {mark}")
    return 0


async def cmd_db_status(args) -> int:
    ready = await db.schema_ready()
    ver = await db.fetchval("SELECT version()")
    counts = {}
    for t in ("runs", "steps", "checkpoints", "connector_health", "human_tasks",
              "sources", "needs", "solutions", "offers", "orders"):
        try:
            counts[t] = int(await db.fetchval(f"SELECT count(*) FROM {t}"))
        except Exception:                                        # noqa: BLE001
            counts[t] = None
    _p(f"{BOLD}postgres{RST}  {str(ver).split(',')[0]}")
    _p(f"{BOLD}schema{RST}    {'ready' if ready else RED + 'NOT READY — run: jpd db migrate' + RST}")
    _p("")
    for t, c in counts.items():
        shown = f"{c}" if c is not None else f"{RED}missing{RST}"
        _p(f"  {t:<20} {shown}")
    return 0 if ready else 1


async def cmd_resume(args) -> int:
    report = await checkpoints.resume_report(args.run)

    if args.json:
        _dump(report)
        return 0

    _p(f"{BOLD}jpd resume{RST}  ·  {settings.env}  ·  {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    _p("")

    cp = report["checkpoint"]
    if cp:
        _p(f"{BOLD}Latest checkpoint{RST}  #{cp['id']} {cp['label']} ({cp['phase']})")
        _p(f"  {DIM}{cp['reason']}{RST}")
        if cp.get("resumable_from"):
            _p(f"  resumable from: {cp['resumable_from']}")
    else:
        _p(f"{BOLD}Latest checkpoint{RST}  {DIM}none written yet{RST}")
    _p("")

    if report["empty"]:
        _p(f"{BOLD}Runs{RST}  {DIM}none — the database is empty.{RST}")
        _p(f"      {DIM}This is a legitimate state, not an error. It is where every")
        _p(f"      build starts.{RST}")
    else:
        _p(f"{BOLD}Resumable runs{RST}")
        if not report["resumable"]:
            _p(f"  {DIM}none — every run has reached a terminal state{RST}")
        for r in report["resumable"]:
            ls = r.get("last_step") or {}
            lease = f"{GRN}free{RST}" if r["lease_expired"] else f"{YEL}HELD{RST} by {r['lease_held_by']}"
            _p(f"  run {r['run_id']} · {r['phase']} · {r['status']} · lease {lease}")
            if ls:
                acc = ls.get("accepted")
                accs = {True: f"{GRN}accepted{RST}", False: f"{RED}NOT accepted{RST}",
                        None: f"{DIM}n/a{RST}"}[acc]
                _p(f"      last step: {ls['step_id']} → {ls['status']} ({accs}), "
                   f"attempt {ls['attempt']}, repairs {ls['repair_count']}")
                _p(f"      {DIM}→ run `jpd verify --last --run {r['run_id']}` before "
                   f"assuming this is done{RST}")
    _p("")

    tasks = report["open_human_tasks"]
    _p(f"{BOLD}Open human tasks{RST}  {len(tasks)}")
    for t in tasks:
        flag = f" {RED}EXPIRED{RST}" if t.get("expired") else ""
        _p(f"  {t['ref']}  {t['title']}{flag}")
        _p(f"      {DIM}why: {t['why']}{RST}")
        if t.get("verify_command"):
            _p(f"      verify: {t['verify_command']}")
    if not tasks:
        _p(f"  {DIM}none{RST}")
    _p("")

    nl = report["non_live_connectors"]
    _p(f"{BOLD}Connectors not live{RST}  {len(nl)}")
    for c in nl:
        _p(f"  {c['connector']:<22} {c['state']:<9} "
           f"fail={c['fail_streak']} zero_yield={c['zero_yield_streak']}")
    if not nl:
        _p(f"  {DIM}all live{RST}")
    _p("")
    return 0


async def cmd_verify(args) -> int:
    """THE RESUME RULE. Exit 2 on disagreement so a script can branch on it."""
    out = await checkpoints.verify_last(args.run)
    if args.json:
        _dump(out)
    else:
        v = out["verdict"]
        colour = {"agrees": GRN, "disagrees": RED, "unverifiable": YEL,
                  "no_steps": DIM}.get(v, "")
        _p(f"{BOLD}verify --last{RST}  →  {colour}{v.upper()}{RST}")
        for k in ("step_id", "run_id", "stored_status", "stored_accepted",
                  "reevaluated_accepted", "reason", "detail"):
            if k in out and out[k] is not None:
                _p(f"  {k:<22} {out[k]}")
    return 2 if out["verdict"] == "disagrees" else 0


async def cmd_checkpoint_write(args) -> int:
    cid = await checkpoints.write(
        phase=args.phase, label=args.label, reason=args.reason,
        run_id=args.run, resumable_from=args.resumable_from,
        state=json.loads(args.state) if args.state else None)
    _p(f"{GRN}checkpoint #{cid} written{RST}  {args.label} ({args.phase})")
    return 0


async def cmd_checkpoint_list(args) -> int:
    rows = await checkpoints.history(args.run, args.limit)
    if not rows:
        _p(f"{DIM}no checkpoints{RST}")
        return 0
    for r in rows:
        run = f"run {r['run_id']}" if r["run_id"] else "global"
        _p(f"  #{r['id']:<5} {r['created_at']:%Y-%m-%d %H:%M}  {r['phase']:<10} "
           f"{r['label']:<28} {DIM}{run}{RST}")
        _p(f"        {DIM}{r['reason']}{RST}")
    return 0


async def cmd_checkpoint_render(args) -> int:
    """Regenerate CHECKPOINT.md, PRESERVING the hand-written 'why' half.

    Blowing away hand-written reasoning on every regeneration is how generated
    docs become worthless. The marker split is what makes generation safe.
    """
    out = Path(args.output) if args.output else CHECKPOINT_MD
    existing = out.read_text() if out.exists() else ""

    # 🔴 REFUSE to clobber a file that has no preservation marker.
    #
    # Without this, pointing `render` at a hand-written CHECKPOINT.md destroys
    # every word of it: split_why() finds no marker, returns "", and the
    # generated header is written over the lot. Institutional memory is the
    # one artifact here with no backup and no way to regenerate it.
    if existing.strip() and checkpoints.WHY_MARKER not in existing:
        _p(f"{RED}refusing to overwrite{RST} {out}")
        _p("")
        _p("  It has content but no preservation marker, so everything in it")
        _p("  would be destroyed. Add this line where the hand-written section")
        _p("  should begin, then re-run:")
        _p("")
        _p(f"      {checkpoints.WHY_MARKER}")
        _p("")
        _p(f"  Everything BELOW that line survives regeneration; everything")
        _p(f"  above it is rewritten each time.")
        return 1

    why = checkpoints.split_why(existing)
    report = await checkpoints.resume_report(None)
    md = checkpoints.render_markdown(report, why)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    _p(f"{GRN}wrote{RST} {out}  ({len(md)} bytes, hand-written section "
       f"{'preserved' if why.strip() else 'empty'})")
    return 0


async def cmd_steps(args) -> int:
    specs = registry.all_steps()
    problems = registry.validate_registry()
    if not specs:
        _p(f"{DIM}no steps registered — phase 0 ships the engine, not the pipeline{RST}")
    for sid, s in sorted(specs.items()):
        _p(f"  {sid:<38} {s.phase:<10} budget ${s.cost_budget_usd:<6.2f} "
           f"timeout {s.timeout_s}s  test={s.test}")
        if s.requires_connectors:
            _p(f"      {DIM}requires: {', '.join(s.requires_connectors)}{RST}")
    if problems:
        _p("")
        for p in problems:
            _p(f"  {RED}PROBLEM{RST} {p}")
        return 1
    return 0


async def cmd_connectors(args) -> int:
    from .connectors import registry as creg
    rows = await connectors.snapshot()
    live = sum(1 for r in rows if r["state"] == "live")
    impl = set(creg.implemented())
    _p(f"{BOLD}connectors{RST}  {live}/{len(rows)} live · {len(impl)} implemented")
    _p("")
    for r in rows:
        colour = {"live": GRN, "degraded": YEL, "dormant": DIM}[r["state"]]
        mark = "" if r["connector"] in impl else f"  {DIM}(no implementation){RST}"
        _p(f"  {r['connector']:<22} {colour}{r['state']:<9}{RST} {r['kind']:<8} "
           f"fail={r['fail_streak']} zero_yield={r['zero_yield_streak']}{mark}")
    return 0


async def cmd_connectors_check(args) -> int:
    """Probe + contract test, feeding the dormancy state machine."""
    from .connectors import health, registry as creg
    await creg.register_all()
    names = [args.name] if args.name else None
    results = await health.check_all(names)

    _p(f"{BOLD}connector health sweep{RST}  {len(results)} checked")
    _p("")
    for r in sorted(results, key=lambda x: (x.state != "live", x.connector)):
        colour = {"live": GRN, "degraded": YEL, "dormant": DIM}[r.state]
        p = f"{GRN}probe{RST}" if r.probe_ok else f"{RED}probe{RST}"
        c = f"{GRN}contract{RST}" if r.contract_ok else f"{RED}contract{RST}"
        _p(f"  {r.connector:<22} {colour}{r.state:<9}{RST} {p} {c}")
        if not r.contract_ok:
            _p(f"      {DIM}{r.detail[:150]}{RST}")
    live = sum(1 for r in results if r.state == "live")
    _p("")
    _p(f"  {GRN}{live}{RST} live of {len(results)}")
    return 0


async def cmd_connectors_harvest(args) -> int:
    from .connectors import health
    if args.name:
        r = await health.harvest(args.name, limit=args.limit)
        _p(f"  {args.name:<22} {r.count} signals ({len(r.admissible)} admissible)")
        _p(f"      {DIM}{r.detail[:160]}{RST}")
        return 0 if r.count else 1

    out = await health.harvest_all(limit=args.limit)
    total = sum(v for v in out.values() if v > 0)
    _p(f"{BOLD}harvest{RST}  {total} signals across {len(out)} connectors")
    _p("")
    for name, n in sorted(out.items(), key=lambda kv: -kv[1]):
        if n < 0:
            _p(f"  {name:<22} {RED}raised{RST}")
        elif n == 0:
            # Zero is a failure signal, not a quiet success.
            _p(f"  {name:<22} {YEL}0{RST}  {DIM}(counts toward dormancy){RST}")
        else:
            _p(f"  {name:<22} {GRN}{n}{RST}")
    s = await health.summary()
    _p("")
    _p(f"  signals stored: {s['signals']} · voices captured: {s['voices']}")
    return 0


async def cmd_connectors_orphans(args) -> int:
    """Drift between the `sources` table and the code, in both directions."""
    from .connectors import registry as creg
    o = await creg.orphans()
    bad = o["rows_without_code"]
    if bad:
        _p(f"{RED}enabled sources with NO implementation{RST} — these can never emit "
           f"and would sit at zero yield forever:")
        for n in bad:
            _p(f"  - {n}")
    else:
        _p(f"{GRN}every enabled source has an implementation{RST}")
    if o["code_without_rows"]:
        _p("")
        _p(f"{YEL}implemented but not enabled in `sources`{RST}:")
        for n in o["code_without_rows"]:
            _p(f"  - {n}")
    return 1 if bad else 0


async def cmd_kill(args) -> int:
    """Kill a run. Proves the lease guard: the worker's next guarded write fails."""
    ok = await lease_mod.request_kill(args.run)
    if ok:
        _p(f"{GRN}kill requested{RST} for run {args.run} — lease cleared. "
           f"Any in-flight step will fail its next guarded write.")
        return 0
    _p(f"{RED}no such run{RST} {args.run}")
    return 1


async def cmd_commerce_status(args) -> int:
    """The revenue picture, and the two numbers that must be zero."""
    rows = await db.fetch(
        "SELECT tier, count(*) AS n, count(*) FILTER (WHERE live) AS live "
        "FROM offers GROUP BY tier ORDER BY tier")
    _p(f"{BOLD}offers{RST}")
    if not rows:
        _p(f"  {DIM}none — no ladder has been created{RST}")
    for r in rows:
        _p(f"  {r['tier']:<14} {r['n']} total, {r['live']} live")
    _p("")

    o = await db.fetchrow(
        "SELECT count(*) AS n, coalesce(sum(amount_minor),0) AS gross, "
        "count(*) FILTER (WHERE status='fulfilled') AS fulfilled FROM orders")
    _p(f"{BOLD}orders{RST}  {o['n']} total · {o['fulfilled']} fulfilled · "
       f"gross {int(o['gross'])/100:.2f}")

    rej = await db.fetch(
        "SELECT reject_reason, count(*) AS n FROM provider_events "
        "WHERE accepted = FALSE GROUP BY reject_reason ORDER BY n DESC LIMIT 5")
    if rej:
        _p(f"{BOLD}rejected webhooks{RST}")
        for r in rej:
            _p(f"  {r['n']:>4}  {DIM}{(r['reject_reason'] or '')[:80]}{RST}")
    _p("")

    from .commerce import fulfilment, notify
    undelivered = await fulfilment.undelivered_paid_orders()
    owed = await notify.pending_and_failed()
    missing = int(await db.fetchval(
        "SELECT count(*) FROM artifacts WHERE missing_since IS NOT NULL") or 0)

    def flag(n: int) -> str:
        return f"{GRN}{n}{RST}" if n == 0 else f"{RED}{n}{RST}"

    _p(f"{BOLD}must be zero{RST}")
    _p(f"  paid but undelivered   {flag(len(undelivered))}")
    _p(f"  buyers not notified    {flag(owed.get('owed', 0))}")
    _p(f"  artifacts missing      {flag(missing)}")
    for u in undelivered[:5]:
        _p(f"    {RED}order {u['order_id']}{RST} {u['buyer_email'] or u['buyer_ref']} "
           f"tier={u['tier']}")
    return 0 if not undelivered and missing == 0 else 1


async def cmd_commerce_orders(args) -> int:
    rows = await db.fetch(
        """
        SELECT o.id, o.provider, o.provider_ref, o.buyer_email, o.buyer_ref,
               o.amount_minor, o.currency, o.signature_valid, o.amount_matched,
               o.status, o.created_at, f.tier
          FROM orders o JOIN offers f ON f.id = o.offer_id
         ORDER BY o.id DESC LIMIT $1
        """, args.last)
    if not rows:
        _p(f"{DIM}no orders — this is what Pimlico has looked like for its whole life{RST}")
        return 0
    for r in rows:
        sig = f"{GRN}sig{RST}" if r["signature_valid"] else f"{RED}NO-SIG{RST}"
        amt = f"{GRN}amt{RST}" if r["amount_matched"] else f"{RED}AMT-MISMATCH{RST}"
        _p(f"  #{r['id']:<5} {r['created_at']:%Y-%m-%d %H:%M} {r['tier']:<13} "
           f"{int(r['amount_minor'])/100:>9.2f} {r['currency']}  {sig} {amt}  "
           f"{r['status']}")
        _p(f"        {DIM}{r['provider']}:{r['provider_ref']} · "
           f"{r['buyer_email'] or r['buyer_ref']}{RST}")
        f = await db.fetch(
            "SELECT tier, status, is_delta FROM fulfilments WHERE entitlement_id = "
            "(SELECT id FROM entitlements WHERE order_id = $1) ORDER BY id", r["id"])
        if f:
            parts = [f"{x['tier']}{'*' if x['is_delta'] else ''}"
                     f"={GRN if x['status']=='delivered' else RED}{x['status']}{RST}"
                     for x in f]
            _p(f"        delivered: {' '.join(parts)}")
    return 0


async def cmd_commerce_contract_test(args) -> int:
    """Run the provider's contract test and feed the dormancy state machine."""
    from .commerce.providers.ghl import GHLProvider
    p = GHLProvider()
    _p(f"{BOLD}probe{RST}")
    pr = await p.probe()
    _p(f"  {GRN + 'ok' + RST if pr.ok else RED + 'FAIL' + RST}  {pr.detail}")
    await connectors.record_probe(p.name, pr.ok, pr.detail)

    _p(f"{BOLD}contract test{RST}")
    tr = await p.contract_test()
    _p(f"  {GRN + 'ok' + RST if tr.ok else RED + 'FAIL' + RST}  {tr.detail}")
    state = await connectors.record_contract_test(p.name, tr.ok, tr.detail)
    _p("")
    colour = {"live": GRN, "degraded": YEL, "dormant": DIM}[state]
    _p(f"  {p.name} is now {colour}{state}{RST}")
    return 0 if tr.ok else 1


async def cmd_commerce_sweep(args) -> int:
    """Re-check every artifact behind a live token. A file that existed at mint
    time can vanish afterwards, and the buyer must not be the monitor."""
    from .commerce import delivery
    out = await delivery.sweep()
    colour = GRN if out["missing"] == 0 else RED
    _p(f"  checked {out['checked']} · present {out['present']} · "
       f"missing {colour}{out['missing']}{RST}")
    return 0 if out["missing"] == 0 else 1


async def cmd_commerce_publish(args) -> int:
    """Make a ladder purchasable. The one outward-facing command in the CLI.

    Prints what WILL go live and refuses on the gate rather than reporting a
    partial success — `offers.publish` raises unless all three tiers exist at
    the provider and all three have an artifact that passed verification.
    """
    from .commerce import offers as offers_mod
    from .commerce.providers.base import ProviderError

    sol = await db.fetchrow(
        "SELECT s.id, s.title, s.need_id FROM solutions s WHERE s.id = $1",
        int(args.solution_id))
    if sol is None:
        _p(f"{RED}solution {args.solution_id} does not exist{RST}")
        return 1

    rows = await db.fetch(
        "SELECT o.tier, o.price_minor, o.currency, o.live, o.checkout_url, "
        "       a.id AS artifact_id, a.offerable "
        "FROM offers o LEFT JOIN LATERAL ("
        "  SELECT id, offerable FROM artifacts WHERE solution_id = o.solution_id "
        "  AND tier = o.tier ORDER BY id DESC LIMIT 1) a ON TRUE "
        "WHERE o.solution_id = $1 ORDER BY o.price_minor", int(args.solution_id))

    _p(f"{BOLD}{sol['title']}{RST}  {DIM}solution {sol['id']} · need {sol['need_id']}{RST}")
    _p("")
    for r in rows:
        mark = f"{GRN}ok{RST}" if r["offerable"] else f"{RED}WITHHELD{RST}"
        _p(f"  {r['tier']:<14} {r['currency']} {r['price_minor']/100:>8.2f}  "
           f"artifact {r['artifact_id'] or '-':<4} {mark}   {DIM}{r['checkout_url']}{RST}")
    _p("")

    try:
        n = await offers_mod.publish(int(args.solution_id))
    except (ProviderError, LookupError) as e:
        _p(f"{RED}refused{RST} {e}")
        return 1

    _p(f"{GRN}LIVE{RST} — {n} offers are now purchasable")
    return 0


async def cmd_commerce_test_ladder(args) -> int:
    """Create a throwaway 3-tier ladder for a real low-value test purchase.

    This is how build phase 1's exit criterion is actually exercised: a REAL
    purchase of each tier, not a simulation. The stub provider proves the code;
    only this proves the integration.
    """
    from .commerce import offers as offers_mod
    from .commerce.providers.ghl import GHLProvider
    from .commerce.providers import base as prov

    if await connectors.state_of("ghl_payments") != "live":
        _p(f"{RED}ghl_payments is not live{RST} — run `jpd commerce contract-test` first.")
        _p(f"{DIM}If it names a missing store id, HT-005 is outstanding.{RST}")
        return 1

    prov.register(GHLProvider())
    need_id = await db.fetchval(
        "INSERT INTO needs (title, status) VALUES ($1,'promoted') RETURNING id",
        f"JPD phase-1 verification {args.label}")
    sol_id = await db.fetchval(
        "INSERT INTO solutions (need_id, title) VALUES ($1,$2) RETURNING id",
        need_id, f"JPD TEST — {args.label} (delete after verification)")

    created = await offers_mod.create_ladder(
        int(sol_id), base_minor=args.base_minor, provider="ghl_payments")
    await offers_mod.publish(int(sol_id))

    _p(f"{GRN}created a live test ladder{RST} for solution {sol_id}")
    _p("")
    for c in created:
        _p(f"  {c.tier:<14} {c.price_minor/100:>8.2f}  {c.checkout_url}")
    _p("")
    _p(f"{YEL}Buy each tier with a real card, then:{RST} jpd commerce orders --last 3")
    _p(f"{DIM}Afterwards, delete the test products in GHL and "
       f"`DELETE FROM solutions WHERE id = {sol_id}`.{RST}")
    return 0


async def cmd_tasks_list(args) -> int:
    from .console import tasks as tasks_mod
    rows = await tasks_mod.open_tasks()
    if not rows:
        _p(f"{DIM}no open human tasks{RST}")
        return 0
    _p(f"{BOLD}open human tasks{RST}  {len(rows)}")
    _p("")
    for t in rows:
        age_h = (datetime.now(timezone.utc) - t["created_at"]).total_seconds() / 3600
        flags = []
        if t["overdue"]:
            flags.append(f"{RED}OVERDUE{RST}")
        if t["unannounced"]:
            flags.append(f"{YEL}NOT POSTED{RST}")
        if t["reply_attempts"]:
            flags.append(f"{YEL}{t['reply_attempts']} rejected replies{RST}")
        _p(f"  {BOLD}{t['ref']}{RST}  [{t['type']}/{t['stream']}]  {age_h:.0f}h old"
           + ("  " + " ".join(flags) if flags else ""))
        _p(f"      {t['title']}")
        _p(f"      {DIM}why: {t['why'][:140]}{RST}")
        if t["run_id"]:
            _p(f"      {DIM}blocking run {t['run_id']} at {t['step_id']}{RST}")
        if t["last_parse_error"]:
            _p(f"      {RED}last error:{RST} {t['last_parse_error'][:140]}")
    _p("")
    _p(f"{DIM}answer one with: jpd tasks reply <REF> \"<your answer>\"{RST}")
    return 0


async def cmd_tasks_show(args) -> int:
    row = await db.fetchrow("SELECT * FROM human_tasks WHERE ref = $1", args.ref)
    if row is None:
        _p(f"{RED}no such task{RST} {args.ref}")
        return 1
    for k in ("ref", "type", "status", "stream", "title", "why", "where_url",
              "verify_command", "run_id", "step_id", "created_at", "expires_at",
              "reply_attempts", "last_parse_error", "skip_reason"):
        v = row[k]
        if v not in (None, "", 0):
            _p(f"  {k:<18} {v}")
    _p(f"  {'reply_schema':<18} {row['reply_schema']}")
    if row["how_md"]:
        _p("")
        _p(f"{BOLD}HOW / PROMPT{RST}")
        _p(row["how_md"])
    if row["reply_json"]:
        _p("")
        _p(f"{BOLD}REPLY{RST}")
        _dump(row["reply_json"])
    return 0


async def cmd_tasks_reply(args) -> int:
    """Answer a task from the CLI.

    🔴 This is the C7 fallback and it is not a convenience. If Telegram is down,
    or HT-001 has not been done yet, the operator can still unblock a run. An
    operator surface with exactly one route in is an operator surface with a
    single point of failure.
    """
    from .console import tasks as tasks_mod
    row = await db.fetchrow("SELECT id, status FROM human_tasks WHERE ref = $1", args.ref)
    if row is None:
        _p(f"{RED}no such task{RST} {args.ref}")
        return 1
    if row["status"] != "open":
        _p(f"{YEL}task is already {row['status']}{RST}")
        return 1

    text = args.text
    if text == "-":
        text = sys.stdin.read()

    parsed = await tasks_mod.apply_reply(int(row["id"]), text)
    if not parsed.ok:
        # A failed parse RE-ASKS; it does not persist. Say exactly what was
        # wrong so the next attempt is informed.
        _p(f"{RED}reply rejected{RST}  {parsed.error}")
        _p(f"{DIM}the task is still open — reply again{RST}")
        return 2
    if parsed.skipped:
        _p(f"{YEL}skipped{RST}  reason recorded: {parsed.skip_reason}")
        return 0
    _p(f"{GRN}accepted{RST}")
    _dump(parsed.value)
    return 0


async def cmd_tasks_expire(args) -> int:
    from .console import tasks as tasks_mod
    out = await tasks_mod.expire_due()
    if not out:
        _p(f"{DIM}nothing overdue{RST}")
        return 0
    for e in out:
        _p(f"  {YEL}EXPIRED{RST} {e['ref']} after {e['age_hours']}h — {e['title']}")
    return 0


async def cmd_telegram_streams(args) -> int:
    from .console.telegram import stream_status
    rows = await stream_status()
    configured = sum(1 for r in rows if r["chat_id"] is not None)
    done = configured == len(rows) and configured > 0
    _p(f"{BOLD}telegram streams{RST}  {configured}/{len(rows)} configured  "
       + (f"{GRN}HT-001 complete{RST}" if done else f"{YEL}HT-001 outstanding{RST}"))
    _p("")
    for r in rows:
        if r["chat_id"] is None:
            _p(f"  {r['stream']:<13} {DIM}not configured{RST}")
        else:
            _p(f"  {r['stream']:<13} chat={r['chat_id']} thread={r['thread_id']}")
        _p(f"      {DIM}{r['purpose']}{RST}")
    if not done:
        _p("")
        _p(f"{DIM}configure with: jpd telegram configure <stream> "
           f"--chat-id -100... --thread-id N{RST}")
        _p(f"{DIM}see runbooks/HT-001-telegram-forum.md{RST}")
    return 0 if done else 1


async def cmd_telegram_configure(args) -> int:
    from .console.telegram import STREAMS, configure_stream
    if args.stream not in STREAMS:
        _p(f"{RED}unknown stream{RST} {args.stream!r}; known: {', '.join(STREAMS)}")
        return 1
    await configure_stream(args.stream, args.chat_id, args.thread_id)
    _p(f"{GRN}configured{RST} {args.stream} → chat={args.chat_id} thread={args.thread_id}")
    return 0


async def cmd_telegram_contract_test(args) -> int:
    from .console.telegram import TelegramClient
    c = TelegramClient()
    pr = await c.probe()
    _p(f"probe          {GRN + 'ok' + RST if pr.ok else RED + 'FAIL' + RST}  {pr.detail}")
    await connectors.record_probe("telegram", pr.ok, pr.detail)
    tr = await c.contract_test()
    _p(f"contract test  {GRN + 'ok' + RST if tr.ok else RED + 'FAIL' + RST}  {tr.detail}")
    state = await connectors.record_contract_test("telegram", tr.ok, tr.detail)
    colour = {"live": GRN, "degraded": YEL, "dormant": DIM}[state]
    _p("")
    _p(f"  telegram is now {colour}{state}{RST}")
    return 0 if tr.ok else 1


async def cmd_telegram_poll(args) -> int:
    from .console import poller
    out = await poller.poll_once(timeout_s=args.timeout)
    _dump(out)
    return 0


async def cmd_channels(args) -> int:
    from .console import channels
    rows = await channels.available()
    live = [c for c in rows if c["live"]]
    _p(f"{BOLD}notification channels{RST}  {len(live)}/{len(rows)} live")
    _p("")
    for c in rows:
        mark = f"{GRN}live{RST}" if c["live"] else f"{DIM}dormant{RST}"
        _p(f"  {c['priority']}. {c['channel']:<10} via {c['connector']:<10} {mark}")
    if not live:
        _p("")
        _p(f"  {YEL}No live channel.{RST} Buyer deliveries will record "
           f"{BOLD}skipped_dormant{RST} — money taken, buyer not told.")
        _p(f"  {DIM}That is counted as OWED and shown by `jpd commerce status`.{RST}")
    return 0 if live else 1


async def cmd_alerts_render(args) -> int:
    """Generate the Prometheus rule file from the rule definitions.

    Generated, not hand-maintained. Pimlico's alert rules exist ONLY as a docker
    config with no source file — `docker cp` returns 0 bytes and the only way to
    read them is `docker exec cat`. Rules you cannot diff are rules nobody
    reviews.

    🔴 Writes to STDOUT by default, and that is a deliberate defence.

    This command runs inside a container. An earlier version defaulted to
    writing a file path on the host — but that path is not mounted, so it wrote
    into the container's ephemeral filesystem and printed
    "wrote … (11 rules)". The same failure had already happened once with
    `checkpoint render`; mounting another directory would fix this instance and
    leave the class alive.

    stdout cannot land in the wrong place: `docker exec` pipes it to the host,
    and the host's shell owns the redirect.

        jpd alerts render > prometheus/rules/jpd.yml
    """
    from .observability import alerts as al
    yaml = al.render_rules_yaml()
    unverified = [r.name for r in al.RULES if r.synthetic is None]

    if args.output in (None, "-"):
        print(yaml, end="")
        # Diagnostics go to STDERR so they never corrupt a redirected file.
        print(f"{len(al.RULES)} rules; {len(unverified)} without a synthetic: "
              f"{', '.join(unverified) or 'none'}", file=sys.stderr)
        return 0

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml)
    written = out.stat().st_size
    _p(f"{GRN}wrote{RST} {out}  ({len(al.RULES)} rules, {written} bytes)")
    _p(f"  {YEL}verify this landed on the HOST{RST} — if this ran in a container "
       f"and {out.parent} is not a bind mount, it did not.")
    if unverified:
        _p(f"  {YEL}{len(unverified)} rule(s) have NO synthetic test{RST} — "
           f"they are unverified detectors:")
        for n in unverified:
            _p(f"    - {n}")
    return 0


async def cmd_alerts_synthetics(args) -> int:
    """Trip every rule that has a synthetic and record whether it fired.

    C2: an untested detector is not a detector. Pimlico shipped eleven rules;
    four of its detectors are silently broken today.
    """
    from .observability import alerts as al
    out = await al.run_synthetics()
    _p(f"{BOLD}synthetic-failure sweep{RST}  {out['fired']}/{out['total']} fired")
    _p("")
    for name, result in sorted(out["results"].items()):
        colour = {"fired": GRN, "did_not_fire": RED,
                  "error": RED, "never_run": YEL}[result]
        _p(f"  {name:<26} {colour}{result}{RST}")
    if out["unverified"]:
        _p("")
        _p(f"  {YEL}unverified:{RST} {', '.join(out['unverified'])}")
    # Non-zero if any rule with a synthetic failed to fire — that is a broken
    # detector, which is worse than a broken feature.
    broken = [k for k, v in out["results"].items() if v in ("did_not_fire", "error")]
    return 1 if broken else 0


async def cmd_alerts_status(args) -> int:
    from .observability import alerts as al
    rows = await db.fetch(
        "SELECT alert_name, last_result, last_tripped_at, detail "
        "FROM alert_synthetics ORDER BY alert_name")
    if not rows:
        _p(f"{YEL}no synthetics have ever run{RST} — run: jpd alerts synthetics")
        return 1
    _p(f"{BOLD}alert rules{RST}  {len(al.RULES)} defined · {len(rows)} with synthetic records")
    _p("")
    for r in rows:
        colour = {"fired": GRN, "did_not_fire": RED, "error": RED,
                  "never_run": YEL}.get(r["last_result"], DIM)
        when = f"{r['last_tripped_at']:%Y-%m-%d %H:%M}" if r["last_tripped_at"] else "never"
        _p(f"  {r['alert_name']:<26} {colour}{r['last_result']:<13}{RST} {when}")
        if r["detail"]:
            _p(f"      {DIM}{r['detail'][:120]}{RST}")
    stale = await al.stale_synthetics()
    if stale:
        _p("")
        _p(f"  {RED}{len(stale)} rule(s) not verified in {al.SYNTHETIC_MAX_AGE_DAYS} days{RST} "
           f"— AlertNeverTripped would fire")
    return 0


async def cmd_scheduler_tick(args) -> int:
    """Run every due job_registry job once. The host timer's entry point."""
    from .runtime import scheduler
    out = await scheduler.tick()
    if out.get("skipped"):
        _p(f"{DIM}tick skipped — {out['skipped']}{RST}")
        return 0
    if not out:
        _p(f"{DIM}nothing due{RST}")
        return 0
    for job, r in out.items():
        if "error" in r:
            _p(f"  {job:<26} {RED}error{RST}  {r['error'][:120]}")
        elif "deferred" in r:
            _p(f"  {job:<26} {YEL}deferred{RST}  {r['deferred']}")
        elif r.get("nothing_to_do"):
            _p(f"  {job:<26} {DIM}nothing to do — stamped{RST}")
        else:
            _p(f"  {job:<26} {GRN}ran{RST}  "
               f"{', '.join(f'{k}={v}' for k, v in r.items() if not isinstance(v, dict))[:110]}")
    return 1 if any("error" in r for r in out.values()) else 0


async def cmd_scheduler_status(args) -> int:
    from .runtime import scheduler
    rows = await db.fetch(
        "SELECT job_name, expected_interval_s, enabled, last_success_at, "
        "       last_attempt_at, "
        "       (last_success_at IS NULL OR last_success_at < now() - "
        "        expected_interval_s * interval '1 second') AS overdue "
        "FROM job_registry ORDER BY job_name")
    due = set(await scheduler.due_jobs())
    _p(f"{BOLD}scheduler{RST}  {len(due)} job(s) due of {len(rows)} registered")
    _p("")
    for r in rows:
        job = r["job_name"]
        if not r["enabled"]:
            state, colour = "disabled", DIM
        elif job in due:
            state, colour = "DUE", YEL
        elif job not in scheduler.DISPATCH and r["overdue"]:
            state, colour = "overdue (app-loop or unmapped)", RED
        else:
            state, colour = "fresh", GRN
        age = "never"
        if r["last_success_at"] is not None:
            age = str(r["last_success_at"].strftime("%Y-%m-%d %H:%M"))
        _p(f"  {job:<28} {colour}{state:<30}{RST} last ok {age}")
    return 0


async def cmd_discover_run(args) -> int:
    """Run Phase A end to end through the real engine."""
    from .discovery import steps as dsteps
    out = await dsteps.run_funnel()
    _p(f"{BOLD}discovery funnel{RST}  run {out['run_id']}")
    _p("")
    for sid, r in out["steps"].items():
        colour = {"succeeded": GRN}.get(r["status"], RED)
        _p(f"  {sid:<24} {colour}{r['status']}{RST}")
        for k, v in (r["data"] or {}).items():
            if k not in ("gaps",):
                _p(f"      {k}: {str(v)[:110]}")
        if r["reason"]:
            _p(f"      {DIM}{r['reason'][:160]}{RST}")
    _p("")
    if out["promoted"]:
        _p(f"  {GRN}PROMOTED {len(out['promoted'])} need(s){RST}: {out['promoted']}")
        return 0
    _p(f"  {YEL}nothing promoted{RST} — run `jpd discover census` to see which gate blocked")
    return 1


async def cmd_discover_census(args) -> int:
    """Which gate blocks what. The question Pimlico could never answer."""
    from .discovery import gates as g
    rows = await g.census()
    if not rows:
        _p(f"{DIM}no gate evaluations yet — run: jpd discover run{RST}")
        return 1
    _p(f"{BOLD}gate census{RST}")
    _p("")
    _p(f"  {'gate':<20} {'pass':>6} {'evals':>6} {'avg':>8} {'thresh':>8} {'margin':>8} {'best':>8}")
    for r in rows:
        rate = f"{r['passes']}/{r['evaluations']}"
        colour = GRN if r["passes"] else RED
        _p(f"  {r['gate']:<20} {colour}{rate:>6}{RST} {r['evaluations']:>6} "
           f"{str(r['avg_value']):>8} {str(r['threshold']):>8} "
           f"{str(r['avg_margin']):>8} {str(r['best_value']):>8}")
    blocking = await g.blocking_gate()
    if blocking:
        _p("")
        _p(f"  {BOLD}blocking most clusters:{RST} " +
           ", ".join(f"{b['gate']} ({b['blocked']})" for b in blocking[:4]))
    return 0


async def cmd_discover_replay(args) -> int:
    """Counterfactual: what WOULD have promoted at different thresholds.

    The payoff for persisting every gate evaluation — tuning becomes a
    measurement over real data instead of a guess plus a rebuild.
    """
    from .discovery import gates as g
    overrides = {}
    for pair in args.override or []:
        k, _, v = pair.partition("=")
        if not v:
            _p(f"{RED}bad override{RST} {pair!r} — use gate=value")
            return 1
        overrides[k.strip()] = float(v)
    out = await g.replay(overrides)
    _p(f"{BOLD}counterfactual replay{RST}  overrides: {out['overrides'] or 'none'}")
    _p("")
    _p(f"  clusters evaluated : {out['clusters_evaluated']}")
    _p(f"  would promote      : {GRN if out['would_promote_count'] else YEL}"
       f"{out['would_promote_count']}{RST}  {out['would_promote'][:12]}")
    if out["still_blocked_by"]:
        _p(f"  still blocked by   : " +
           ", ".join(f"{k} ({v})" for k, v in out["still_blocked_by"].items()))
    return 0


async def cmd_discover_needs(args) -> int:
    rows = await db.fetch(
        "SELECT id, title, status, score, audience, frequency, severity, "
        "cross_source, distinct_voices, gap FROM needs ORDER BY id DESC LIMIT $1",
        args.limit)
    if not rows:
        _p(f"{DIM}no needs — nothing has been promoted{RST}")
        return 1
    for r in rows:
        _p(f"  #{r['id']} {BOLD}{r['title'][:80]}{RST}  [{r['status']}] score={r['score']}")
        _p(f"      freq={r['frequency']} sev={r['severity']} "
           f"cross_source={r['cross_source']} voices={r['distinct_voices']} "
           f"gap={r['gap'] if r['gap'] is not None else 'NULL (deferred to Phase B)'}")
        _p(f"      {DIM}{(r['audience'] or '')[:110]}{RST}")
    return 0


async def cmd_research_run(args) -> int:
    """Run Phase B for one need, end to end through the engine."""
    from .research import steps as rsteps
    out = await rsteps.run_research(args.need_id)
    _p(f"{BOLD}research{RST}  need {args.need_id} · run {out['run_id']}")
    _p("")
    for sid, r in out["steps"].items():
        colour = {"succeeded": GRN}.get(r["status"], RED)
        _p(f"  {sid:<30} {colour}{r['status']}{RST}")
        for k, v in (r["data"] or {}).items():
            if k not in ("need_id",):
                _p(f"      {k}: {str(v)[:105]}")
        if r["reason"]:
            _p(f"      {DIM}{r['reason'][:170]}{RST}")
    if "stopped_at" in out:
        _p("")
        _p(f"  {YEL}stopped at {out['stopped_at']}{RST}")
        return 1
    _p("")
    _p(f"  {GRN}dossier complete{RST}")
    return 0


async def cmd_research_dossier(args) -> int:
    row = await db.fetchrow(
        "SELECT body, evidence_count, claim_count FROM dossiers "
        "WHERE need_id=$1 AND kind='research'", args.need_id)
    if row is None:
        _p(f"{DIM}no research dossier for need {args.need_id}{RST}")
        return 1
    body = row["body"]
    _dump(json.loads(body) if isinstance(body, str) else body)
    return 0


async def cmd_research_evidence(args) -> int:
    rows = await db.fetch(
        "SELECT id, kind, http_status, live_at_capture, bytes, "
        "left(sha256,12) AS sha, left(coalesce(title,url),68) AS what, url "
        "FROM evidence WHERE need_id=$1 ORDER BY id", args.need_id)
    if not rows:
        _p(f"{DIM}no evidence for need {args.need_id}{RST}")
        return 1
    live = sum(1 for r in rows if r["live_at_capture"])
    _p(f"{BOLD}evidence{RST}  {len(rows)} rows · {GRN}{live} live{RST}")
    _p("")
    for r in rows:
        mark = f"{GRN}live{RST}" if r["live_at_capture"] else f"{RED}dead{RST}"
        _p(f"  #{r['id']:<4} {mark} {r['kind']:<14} {str(r['http_status']):>3} "
           f"{str(r['bytes'] or 0):>7}b  {r['sha']}  {r['what']}")
    _p("")
    _p(f"{DIM}verify they still resolve: jpd research verify {args.need_id}{RST}")
    return 0


async def cmd_research_verify(args) -> int:
    """Re-fetch every cited URL and check the hash still matches."""
    from .research import evidence as rev
    out = await rev.verify_live(args.need_id)
    _p(f"  checked {out['checked']} · {GRN}live {out['still_live']}{RST} · "
       f"{RED}dead {out['dead']}{RST} · {YEL}changed {out['changed']}{RST}")
    if out["changed"]:
        _p(f"  {YEL}'changed' is the dangerous one{RST} — the link resolves but the "
           f"bytes differ, so a claim beside it may no longer be supported.")
    return 0 if not out["dead"] else 1


async def cmd_research_claims(args) -> int:
    rows = await db.fetch(
        "SELECT c.id, c.kind, c.confidence, left(c.text,96) AS text, "
        "e.url, left(e.sha256,10) AS sha FROM claims c "
        "JOIN evidence e ON e.id = c.evidence_id WHERE c.need_id=$1 ORDER BY c.kind, c.id",
        args.need_id)
    if not rows:
        _p(f"{DIM}no claims for need {args.need_id}{RST}")
        return 1
    uncited = await db.fetchval(
        "SELECT count(*) FROM claims WHERE need_id=$1 AND evidence_id IS NULL", args.need_id)
    _p(f"{BOLD}claims{RST}  {len(rows)} · uncited: "
       f"{GRN if not uncited else RED}{uncited}{RST}")
    _p("")
    for r in rows:
        _p(f"  [{r['kind']}] {r['text']}")
        _p(f"      {DIM}cite {r['sha']} · {(r['url'] or '')[:88]}{RST}")
    return 0


async def cmd_research_check(args) -> int:
    """Fact-check claims that no artifact cites yet.

    Solution research produces claims BEFORE anything cites them, and
    `market copy` draws only on `supported IS TRUE` — so an unverified claim is
    invisible to the copy generator no matter how good it is.
    """
    from .forge import verify as vf
    out = await vf.verify_claims(args.need_id, only_unverified=not args.all)
    if not out["checked"]:
        _p(f"{GRN}nothing to check{RST} — every claim for need "
           f"{args.need_id} already has a verdict.")
        return 0
    _p(f"{BOLD}claim check{RST}  need {args.need_id} · {out['checked']} claim(s)")
    _p("")
    for d in out["detail"]:
        mark = f"{GRN}supported{RST}" if d["supported"] else f"{RED}unsupported{RST}"
        _p(f"  #{d['claim_id']:<4} {mark}  {d['text'][:78]}")
        if not d["supported"]:
            _p(f"        {DIM}{d['why'][:100]}{RST}")
    _p("")
    _p(f"  {GRN}{out['supported']}{RST} supported · "
       f"{YEL}{out['unsupported']}{RST} unsupported")
    _p(f"  {DIM}Only supported claims reach the copy generator.{RST}")
    return 0


async def cmd_research_solution(args) -> int:
    """Capture evidence for the REMEDY, and extract claims that SUPPORT it.

    🔴 Phase B's normal path extracts GAPS — what a page is missing — which is
    the right input for deciding what to build and the wrong input for writing
    sales copy. Measured 2026-08-09 on need 13: headline/subhead/objections hit
    100% citation coverage while benefits/faq sat at 0-50%, because the first
    three describe the PROBLEM and the last two describe the SOLUTION, and every
    claim in the database was a gap claim.
    """
    from .research import dossier, evidence as ev

    queries = await dossier.solution_queries(args.need_id, n=args.queries)
    if not queries:
        _p(f"{RED}no solution queries produced{RST} — run "
           f"`jpd market position {args.need_id}` first so there is a promise "
           f"to research.")
        return 1
    _p(f"{BOLD}solution research{RST}  need {args.need_id}")
    _p("")
    for q in queries:
        _p(f"  {DIM}query{RST} {q}")

    before = await ev.stats(args.need_id)
    captured = 0
    for q in queries:
        captured += len(await ev.capture_search(args.need_id, q, limit=args.limit))
    after = await ev.stats(args.need_id)

    _p("")
    _p(f"  captured {captured} page(s) · usable "
       f"{int(before.get('usable') or 0)} → {int(after.get('usable') or 0)} · "
       f"domains {int(after.get('domains') or 0)}")

    out = await dossier.support_analysis(args.need_id)
    _p(f"  extracted {GRN}{out['claims']}{RST} supporting claim(s) "
       f"from {out['pages_read']} page(s)")
    _p("")
    _p(f"  {DIM}These are `support` claims, not gaps. Verify them with "
       f"`jpd forge reverify {args.need_id}`, then regenerate copy with "
       f"`jpd market recopy {args.need_id} --below-floor`.{RST}")
    return 0


async def cmd_forge_repair(args) -> int:
    """Regenerate ONE section. One LLM call, not a $6-9 rebuild."""
    from .forge import build, verify as vf
    try:
        out = await build.repair_section(args.need_id, args.tier, args.section,
                                         extra_brief=args.brief or "")
    except (ValueError, LookupError, RuntimeError) as e:
        _p(f"{RED}{e}{RST}")
        return 1

    ok = out["meets_minimum"]
    colour = GRN if ok else YEL
    _p(f"{BOLD}repaired{RST} {out['tier']}/{out['section']}  "
       f"{out['words_before']} → {colour}{out['words_after']}{RST} words "
       f"{DIM}(minimum {out['min_words']}){RST}")
    if out["claim_ids"]:
        _p(f"  cites {len(out['claim_ids'])} claim(s): "
           f"{', '.join(str(c) for c in out['claim_ids'][:8])}")
    if not ok:
        _p(f"  {YEL}still below the minimum{RST} — repair again, or with "
           f"`--brief` to tell it what was missing")

    # Re-package and re-check STRUCTURE only. Structural is free; the factual
    # pass is 14 LLM calls and belongs to `jpd forge reverify`, so a repair
    # never silently bills for verification the operator did not ask for.
    secs = build.load_draft(args.need_id, args.tier)
    packed = await build.package(args.need_id, args.tier, secs)
    res = await vf.structural(int(packed["artifact_id"]))
    _p("")
    _p(f"  repackaged → artifact #{packed['artifact_id']} · "
       f"{packed['words']:,} words · {packed['sha256'][:12]}")
    mark = GRN if res.structural_ok else YEL
    _p(f"  structural {mark}{'PASS' if res.structural_ok else 'FAIL'}{RST}")
    for label, vals in (("missing", res.missing_sections),
                        ("thin", res.thin_sections),
                        ("placeholder", res.placeholders)):
        if vals:
            _p(f"    {DIM}{label}: {', '.join(map(str, vals))[:110]}{RST}")
    _p(f"  {DIM}factual state is unchanged — run "
       f"`jpd forge reverify {args.need_id}` to re-check claims{RST}")
    return 0 if ok else 1


async def cmd_market_recopy(args) -> int:
    """Regenerate only the copy blocks that need it."""
    from .market import copy as mcopy
    try:
        out = await mcopy.recopy(args.need_id, tier=args.tier, block=args.block,
                                 below_floor_only=args.below_floor)
    except ValueError as e:
        _p(f"{RED}{e}{RST}")
        return 1
    if not out:
        _p(f"{GRN}nothing to do{RST} — no block matched "
           f"{DIM}(every targeted block already clears the floor){RST}")
        return 0

    _p(f"{BOLD}recopied{RST} {len(out)} block(s)  "
       f"{DIM}floor {mcopy.COVERAGE_FLOOR:.0f}%{RST}")
    _p("")
    still_low = 0
    for b in out:
        pct = b["citation_pct"]
        before = b.get("before")
        colour = GRN if pct >= mcopy.COVERAGE_FLOOR else RED
        delta = "" if before is None else f"  {DIM}was {before:.0f}%{RST}"
        _p(f"  {b['tier']:<13} {b['block']:<11} {colour}{pct:>5.1f}%{RST}  "
           f"{b['citation_checkable']:>2} checkable{delta}")
        if pct < mcopy.COVERAGE_FLOOR:
            still_low += 1
            for ex in (b.get("examples") or [])[:2]:
                _p(f"      {DIM}uncited: {ex[:96]}{RST}")
        for sp in (b.get("service_promises") or [])[:2]:
            _p(f"      {RED}service promise{RST} {DIM}{sp[:92]}{RST}")
        for ph in (b.get("placeholders") or [])[:3]:
            _p(f"      {RED}unfinished{RST} {DIM}{ph}{RST}")
    _p("")
    promising = sum(1 for b in out if b.get("service_promises"))
    unfinished = sum(1 for b in out if b.get("placeholders"))
    if unfinished:
        _p(f"  {RED}{unfinished} block(s) contain unfinished-work markers.{RST} "
           f"{DIM}A buyer reads the sales page — \"[Price would go here]\" is "
           f"the author talking to themselves.{RST}")
    if promising:
        _p(f"  {RED}{promising} block(s) promise that WE act.{RST} "
           f"{DIM}The product is a document the buyer follows themselves — a "
           f"sentence like \"we restore your login\" is a promise nobody can "
           f"keep, and no evidence could ever cite it.{RST}")
    if still_low:
        _p(f"  {YEL}{still_low} block(s) still below the floor.{RST} "
           f"{DIM}An assertion that cannot be cited should be REMOVED from the "
           f"copy, not annotated — a buyer reads the promise, not the caveat.{RST}")
    else:
        _p(f"  {GRN}every regenerated block clears the floor{RST}")
    return 0 if not (still_low or promising or unfinished) else 1


async def cmd_market_show(args) -> int:
    """Read-only. What copy exists, and whether it cites itself."""
    pos = await db.fetchrow(
        "SELECT pain_phrase, audience, promise, proof FROM positioning "
        " WHERE need_id=$1", args.need_id)
    _p(f"{BOLD}market{RST}  need {args.need_id}")
    _p("")
    if pos is None:
        _p(f"  {YEL}no positioning{RST} — run `jpd market position "
           f"{args.need_id}`")
    else:
        _p(f"  {DIM}pain{RST}     {pos['pain_phrase']}")
        _p(f"  {DIM}audience{RST} {pos['audience']}")
        _p(f"  {DIM}promise{RST}  {pos['promise']}")
        _p(f"  {DIM}proof{RST}    {(pos['proof'] or '')[:150]}")

    rows = await db.fetch(
        "SELECT tier, block, citation_pct, citation_checkable, length(body) AS n "
        "  FROM copy_blocks WHERE need_id=$1 ORDER BY tier, block", args.need_id)
    if not rows:
        _p("")
        _p(f"  {YEL}no copy blocks{RST} — run `jpd market copy {args.need_id}` "
           f"{DIM}(spends){RST}")
    else:
        from .market.copy import COVERAGE_FLOOR
        _p("")
        _p(f"  {BOLD}copy blocks{RST}  {DIM}coverage floor {COVERAGE_FLOOR:.0f}%{RST}")
        for r in rows:
            pct = float(r["citation_pct"])
            colour = GRN if pct >= COVERAGE_FLOOR else RED
            _p(f"    {r['tier']:<13} {r['block']:<11} {colour}{pct:>5.1f}%{RST}  "
               f"{r['citation_checkable']:>3} checkable · {r['n']:>4} chars")

    page = await db.fetchrow(
        "SELECT sha256, bytes, tiers, citation_pct, publishable, storage_uri "
        "  FROM sales_pages WHERE need_id=$1", args.need_id)
    _p("")
    if page is None:
        _p(f"  {DIM}no sales page — run `jpd market page {args.need_id}`{RST}")
    else:
        state = (f"{GRN}PUBLISHABLE{RST}" if page["publishable"]
                 else f"{YEL}not publishable{RST}")
        _p(f"  {BOLD}sales page{RST}  {state}  {page['tiers']} tier(s) · "
           f"{page['bytes']:,} bytes · {float(page['citation_pct']):.1f}% cited")
        _p(f"    {DIM}{(page['storage_uri'] or '').replace('file://','')}{RST}")
    return 0


async def cmd_market_position(args) -> int:
    from .market import copy as mcopy
    try:
        out = await mcopy.build_positioning(args.need_id)
    except (ValueError, LookupError) as e:
        _p(f"{RED}{e}{RST}")
        return 1
    _p(f"{GRN}positioning written{RST} for need {args.need_id}  "
       f"{DIM}({out['voices_used']} voice quotes, "
       f"{out['claims_available']} supported claims){RST}")
    for k in ("pain_phrase", "audience", "promise", "proof"):
        _p(f"  {DIM}{k:<12}{RST} {str(out.get(k, ''))[:150]}")
    return 0


async def cmd_market_copy(args) -> int:
    """SPENDS. One LLM call per block per tier — 15 calls."""
    from .market import steps as msteps
    out = await msteps.run_market(args.need_id, stop_after="market.copy")
    r = (out["steps"].get("market.copy") or {})
    d = r.get("data") or {}
    _p(f"{BOLD}market copy{RST}  need {args.need_id} · run {out['run_id']}")
    _p("")
    for sid, s in out["steps"].items():
        colour = GRN if s["status"] == "succeeded" else RED
        _p(f"  {sid:<22} {colour}{s['status']}{RST}"
           + (f"  {DIM}{(s['reason'] or '')[:90]}{RST}" if s.get("reason") else ""))
    if d:
        below = d.get("below_floor", 0)
        colour = GRN if not below else RED
        _p("")
        _p(f"  {d.get('blocks_stored', 0)} blocks · worst coverage "
           f"{colour}{d.get('worst_coverage', 0):.1f}%{RST} · floor "
           f"{d.get('floor', 0):.0f}%")
        for line in (d.get("detail") or []):
            _p(f"    {YEL}below floor{RST} {line}")
        if below:
            _p("")
            _p(f"  {YEL}Copy is stored but the step did NOT pass.{RST} "
               f"{DIM}A block that cannot cite its claims is not shippable "
               f"marketing — see `jpd market show {args.need_id}`.{RST}")
    return 0 if "stopped_at" not in out else 1


async def cmd_market_page(args) -> int:
    from .market import pages
    try:
        out = await pages.build_page(args.need_id)
    except ValueError as e:
        _p(f"{RED}{e}{RST}")
        return 1
    state = f"{GRN}PUBLISHABLE{RST}" if out["publishable"] else f"{YEL}not publishable{RST}"
    _p(f"{GRN}page built{RST}  {state}")
    for b in out.get("blockers") or []:
        _p(f"  {RED}blocked{RST}  {b}")
    _p(f"  {out['tiers']} tier(s), {out['sellable']} with a live offer")
    _p(f"  {out['citation_pct']:.1f}% of checkable assertions cited")
    _p(f"  {out['bytes']:,} bytes · {out['sha256'][:12]}")
    _p(f"  {DIM}{out['path']}{RST}")
    if not out["publishable"]:
        _p("")
        _p(f"  {DIM}A page is publishable only when EVERY tier on it has a live "
           f"offer with a checkout url and coverage clears the floor. A Buy "
           f"button that goes nowhere burns the audience once.{RST}")
    return 0


async def cmd_market_launch(args) -> int:
    """Plans and reports. NEVER sends — sending is a separate human decision."""
    import os as _os
    from .market import outreach
    base = _os.environ.get("JPD_UNSUBSCRIBE_BASE", "")
    if not base:
        _p(f"{RED}JPD_UNSUBSCRIBE_BASE is not set{RST} — refusing to plan "
           f"outreach without an unsubscribe path for every recipient.")
        return 1
    plan = await outreach.plan_launch(args.need_id, base)
    _p(f"{BOLD}launch plan{RST}  need {args.need_id}  {DIM}(nothing is sent){RST}")
    _p("")
    _p(f"  {GRN}{len(plan['eligible'])}{RST} eligible · "
       f"{YEL}{len(plan['blocked'])}{RST} blocked · "
       f"{DIM}{len(plan['excluded'])} competitors excluded{RST}  "
       f"of {plan['total_voices']} voices")
    for b in plan["blocked"][:12]:
        _p(f"    {YEL}blocked{RST} voice {b['voice_id']} "
           f"({b.get('name') or 'unnamed'}): {b['why']}")
    _p("")
    try:
        outreach.assert_sendable(plan)
    except PermissionError as e:
        _p(f"  {YEL}REFUSED{RST} {str(e)[:400]}")
        _p(f"  {DIM}This is the designed behaviour for a community-scraped "
           f"audience, not a fault.{RST}")
        return 0
    _p(f"  {GRN}every recipient passes{RST} — approval is still required; "
       f"run the step to raise the decision card.")
    return 0


async def cmd_forge_reverify(args) -> int:
    """Re-package and re-verify from the SAVED DRAFTS. No generation, no spend.

    🔴 WHY THIS EXISTS. `jpd forge run` re-generates every time: the engine's
    idempotency lookup is scoped `WHERE run_id = $1 AND step_id = $2`, and every
    invocation opens a new run_id, so the $6 generation step never hits its
    cache across runs. Need 13 was regenerated twice in one day purely to re-run
    a verification, and each regeneration also OVERWRITES the drafts — so the
    artifact you were reasoning about is gone by the time you look again.

    Verification is the step you actually iterate: fix an excerpt window, fix a
    stripper, re-capture evidence, then ask "does it pass now?". That question
    should not cost nine dollars and a fresh set of artifacts.

    Generation output is deliberately reused as-is, so the words on disk are
    the words that were verified.
    """
    from .forge import build, verify as vf
    from .forge.plan import TIER_ORDER

    built = {t: build.load_draft(args.need_id, t) for t in TIER_ORDER}
    built = {t: v for t, v in built.items() if v}
    if not built:
        _p(f"{RED}no drafts on disk for need {args.need_id}{RST} — "
           f"run `jpd forge run {args.need_id}` once to generate them")
        return 1

    _p(f"{BOLD}forge reverify{RST}  need {args.need_id} · "
       f"{len(built)} tier(s) from drafts · {DIM}no generation{RST}")
    _p("")

    # Shared across tiers: they cite the SAME claims, so each is checked once.
    claim_verdicts: dict[int, tuple[bool, str]] = {}
    offerable = 0
    for tier in TIER_ORDER:
        secs = built.get(tier) or []
        if not secs:
            continue
        packed = await build.package(args.need_id, tier, secs)
        res = await vf.verify(int(packed["artifact_id"]), claim_verdicts)
        ok = res.structural_ok and res.factual_ok
        offerable += 1 if ok else 0
        colour = GRN if ok else YEL
        _p(f"  {tier:<14} {colour}{'OFFERABLE' if ok else 'withheld'}{RST}  "
           f"{packed['words']:,}w · claims {res.claims_supported}/"
           f"{res.claims_checked}")
        for label, vals in (("missing", res.missing_sections),
                            ("thin", res.thin_sections),
                            ("placeholder", res.placeholders)):
            if vals:
                _p(f"      {DIM}{label}: {', '.join(map(str, vals))[:110]}{RST}")
        if res.unsupported:
            _p(f"      {DIM}unsupported: {len(res.unsupported)}{RST}")

    distinct_bad = sum(1 for ok, _ in claim_verdicts.values() if not ok)
    _p("")
    _p(f"  {DIM}{len(claim_verdicts)} distinct claims checked once each "
       f"({distinct_bad} unsupported){RST}")
    _p(f"  {GRN if offerable else YEL}{offerable}/{len(built)} offerable{RST}")
    return 0


async def cmd_forge_run(args) -> int:
    from .forge import steps as fsteps
    out = await fsteps.run_forge(args.need_id)
    _p(f"{BOLD}forge{RST}  need {args.need_id} · run {out['run_id']}")
    _p("")
    for sid, r in out["steps"].items():
        colour = {"succeeded": GRN}.get(r["status"], RED)
        _p(f"  {sid:<26} {colour}{r['status']}{RST}")
        for k, v in (r["data"] or {}).items():
            if k not in ("need_id", "verdicts", "detail"):
                _p(f"      {k}: {str(v)[:105]}")
        if r["reason"]:
            _p(f"      {DIM}{r['reason'][:180]}{RST}")
    if "stopped_at" in out:
        _p(f"\n  {YEL}stopped at {out['stopped_at']}{RST}")
        return 1
    _p(f"\n  {GRN}three artifacts built and verified{RST}")
    return 0


async def cmd_forge_artifacts(args) -> int:
    rows = await db.fetch(
        "SELECT id, tier, words, sections, bytes, left(sha256,12) AS sha, "
        "structural_ok, factual_ok, offerable, storage_uri "
        "FROM artifacts WHERE need_id=$1 ORDER BY id", args.need_id)
    if not rows:
        _p(f"{DIM}no artifacts for need {args.need_id}{RST}")
        return 1
    from pathlib import Path
    _p(f"{BOLD}artifacts{RST}  need {args.need_id}")
    _p("")
    for r in rows:
        path = Path((r["storage_uri"] or "").replace("file://", ""))
        on_disk = path.is_file()
        s_ok = f"{GRN}struct{RST}" if r["structural_ok"] else f"{RED}struct{RST}"
        f_ok = f"{GRN}factual{RST}" if r["factual_ok"] else f"{RED}factual{RST}"
        sell = f"{GRN}OFFERABLE{RST}" if r["offerable"] else f"{YEL}withheld{RST}"
        disk = f"{GRN}on disk{RST}" if on_disk else f"{RED}FILE MISSING{RST}"
        _p(f"  #{r['id']} {BOLD}{r['tier']:<13}{RST} {r['words']:>6} words · "
           f"{r['sections']} sections · {r['sha']} · {disk}")
        _p(f"      {s_ok} {f_ok}  {sell}")
    # The count that matters — via `artifact_claims`, the JOIN TABLE.
    #
    # 🔴 These two queries used to read `claims.deliverable_id`, and that column
    # is DEAD. Migration 014 replaced it with `artifact_claims` because the
    # tiers are supersets and legitimately cite the SAME claim, and packaging
    # has written only the join table ever since — nothing sets deliverable_id
    # any more. It holds whatever the last pre-migration package left behind.
    #
    # So this reported the truth only for claims old enough to still carry the
    # legacy value. Demonstrated 2026-08-09 on need 13: adding ONE unsupported
    # claim, linked the way packaging links today, gave "2 unsupported" here
    # against a true count of 3. The miss is silent and it fails toward a false
    # all-clear, which is the same shape as every vacuous pass this system has
    # been bitten by.
    #
    # DISTINCT because three tiers cite one claim; counting rows would report a
    # single defect three times.
    uncited = await db.fetchval(
        "SELECT count(DISTINCT c.id) FROM artifact_claims ac "
        "  JOIN claims c ON c.id = ac.claim_id "
        "  JOIN artifacts a ON a.id = ac.artifact_id "
        " WHERE a.need_id = $1 AND c.evidence_id IS NULL",
        args.need_id)
    unsupported = await db.fetchval(
        "SELECT count(DISTINCT c.id) FROM artifact_claims ac "
        "  JOIN claims c ON c.id = ac.claim_id "
        "  JOIN artifacts a ON a.id = ac.artifact_id "
        " WHERE a.need_id = $1 AND c.supported = FALSE",
        args.need_id)
    # Citation coverage, read from the verdict each artifact stored.
    cov = await db.fetch(
        "SELECT tier, (verify_detail->>'citation_cited')::int AS cited, "
        "       (verify_detail->>'citation_checkable')::int AS checkable, "
        "       (verify_detail->>'citation_pct')::float AS pct "
        "  FROM artifacts WHERE need_id=$1 AND verify_detail ? 'citation_pct' "
        " ORDER BY id", args.need_id)

    _p("")
    _p(f"  unsupported claims: {GRN if not unsupported else YEL}{unsupported}{RST}"
       f"   uncited claim rows: {uncited} "
       f"{DIM}(always 0 — claims.evidence_id is NOT NULL){RST}")
    if cov:
        _p("")
        _p(f"  {BOLD}citation coverage{RST} {DIM}— checkable assertions carrying "
           f"a [claim N] marker{RST}")
        for r in cov:
            pct = r["pct"] or 0.0
            colour = GRN if pct >= 90 else (YEL if pct >= 70 else RED)
            _p(f"    {r['tier']:<13} {colour}{pct:>5.1f}%{RST}  "
               f"{r['cited']}/{r['checkable']}")
        _p(f"  {DIM}measured, not gating — see forge/verify.py citation_coverage(){RST}")
    return 0


async def cmd_forge_show(args) -> int:
    from pathlib import Path
    row = await db.fetchrow(
        "SELECT storage_uri FROM artifacts WHERE need_id=$1 AND tier=$2",
        args.need_id, args.tier)
    if row is None:
        _p(f"{RED}no {args.tier} artifact for need {args.need_id}{RST}")
        return 1
    path = Path((row["storage_uri"] or "").replace("file://", ""))
    if not path.is_file():
        _p(f"{RED}file missing on disk{RST}: {path}")
        return 1
    print(path.read_text()[: args.chars])
    return 0


async def cmd_doctor(args) -> int:
    """Preflight. Reports what is wrong, and exits non-zero if anything is."""
    problems: list[str] = []
    _p(f"{BOLD}jpd doctor{RST}")
    _p("")

    try:
        await db.fetchval("SELECT 1")
        _p(f"  postgres            {GRN}reachable{RST}")
    except Exception as e:                                       # noqa: BLE001
        _p(f"  postgres            {RED}UNREACHABLE{RST} {e}")
        problems.append("postgres unreachable")
        _p("")
        _p(f"{RED}{len(problems)} problem(s){RST}")
        return 1

    ready = await db.schema_ready()
    _p(f"  schema              {GRN + 'ready' + RST if ready else RED + 'NOT READY' + RST}")
    if not ready:
        problems.append("schema not migrated — run: jpd db migrate")

    try:
        pending = [r for r in await db.migrate(dry_run=True) if r["action"] == "would_apply"]
        if pending:
            _p(f"  migrations          {YEL}{len(pending)} pending{RST}")
            problems.append(f"{len(pending)} pending migrations")
        else:
            _p(f"  migrations          {GRN}up to date{RST}")
    except db.MigrationDrift as e:
        _p(f"  migrations          {RED}DRIFT{RST} {e}")
        problems.append("migration drift")

    reg = registry.validate_registry()
    _p(f"  step registry       {GRN + 'ok' + RST if not reg else RED + str(len(reg)) + ' problems' + RST}"
       f" ({len(registry.all_steps())} steps)")
    problems.extend(reg)

    if ready:
        rows = await connectors.snapshot()
        live = sum(1 for r in rows if r["state"] == "live")
        _p(f"  connectors          {live}/{len(rows)} live")

        stale = await db.fetchval(
            "SELECT count(*) FROM runs WHERE status = 'running' AND "
            "(lease_expires_at IS NULL OR lease_expires_at < now())")
        if int(stale or 0):
            _p(f"  orphaned runs       {YEL}{stale}{RST} running with an expired lease")
            problems.append(f"{stale} orphaned runs")
        else:
            _p(f"  orphaned runs       {GRN}none{RST}")

        expired = await db.fetchval(
            "SELECT count(*) FROM human_tasks WHERE status = 'open' AND expires_at < now()")
        if int(expired or 0):
            _p(f"  expired tasks       {YEL}{expired}{RST} open past their deadline")
            problems.append(f"{expired} expired human tasks")

        # A status that is NULL is the exact defect this schema forbids. If the
        # count is ever non-zero the CHECK constraint has been dropped.
        nulls = await db.fetchval("SELECT count(*) FROM steps WHERE status IS NULL")
        if int(nulls or 0):
            _p(f"  null step statuses  {RED}{nulls}{RST} — the NOT NULL constraint is gone")
            problems.append("null step statuses present")

    _p("")
    creds = credential_status()
    have = sum(1 for v in creds.values() if v)
    _p(f"  credentials         {have}/{len(creds)} present {DIM}(booleans only — "
       f"values are never printed){RST}")
    for k, v in creds.items():
        _p(f"      {'✓' if v else '·'} {k}")

    _p("")
    if problems:
        _p(f"{RED}{len(problems)} problem(s):{RST}")
        for p in problems:
            _p(f"  - {p}")
        return 1
    _p(f"{GRN}all checks passed{RST}")
    return 0


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------

async def cmd_ui(args) -> int:
    """Render the dashboard to a file or stdout.

    🔴 THIS EXISTS SO THE DASHBOARD NEEDS NO PUBLISHED PORT. The page has no
    authentication, and swarm cannot publish it to loopback only — ingress mode
    discards the host-IP prefix, so `127.0.0.1:8905:8905` silently binds
    0.0.0.0. Measured 2026-08-08: that line put an unauthenticated dashboard on
    a public interface for ~3 minutes, with no firewall DROP because 8905 was
    absent from SWARM_PORTS.

    So the operator surface is a FILE, not a socket:

        jpd ui --out /tmp/jpd.html          # on the box
        ssh <host> 'jpd ui' > jpd.html      # from a laptop, over ssh's own auth

    No port, no listener, nothing to firewall, and it travels over a channel
    that is already authenticated.
    """
    from .console import dashboard
    html = await dashboard.page()
    if args.out == "-":
        print(html)
    else:
        from pathlib import Path
        Path(args.out).write_text(html)
        _p(f"wrote {args.out}  ({len(html):,} bytes)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jpd", description="JarvisProductDevelopment runtime control")
    p.add_argument("--dsn", help="override JPD_PG_DSN")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("db", help="schema management").add_subparsers(dest="sub", required=True)
    m = d.add_parser("migrate"); m.add_argument("--dry-run", action="store_true")
    m.set_defaults(fn=cmd_db_migrate)
    d.add_parser("status").set_defaults(fn=cmd_db_status)

    r = sub.add_parser("resume", help="what is in flight and what to do next")
    r.add_argument("--run", type=int, help="restrict to one run")
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=cmd_resume)

    v = sub.add_parser("verify", help="re-run the last step's acceptance predicate")
    v.add_argument("--last", action="store_true", default=True)
    v.add_argument("--run", type=int)
    v.add_argument("--json", action="store_true")
    v.set_defaults(fn=cmd_verify)

    c = sub.add_parser("checkpoint").add_subparsers(dest="sub", required=True)
    cw = c.add_parser("write")
    cw.add_argument("label")
    cw.add_argument("--reason", required=True, help="mandatory — why this checkpoint exists")
    cw.add_argument("--phase", default="BUILD")
    cw.add_argument("--run", type=int)
    cw.add_argument("--resumable-from")
    cw.add_argument("--state", help="JSON blob of machine state")
    cw.set_defaults(fn=cmd_checkpoint_write)
    cl = c.add_parser("list"); cl.add_argument("--run", type=int)
    cl.add_argument("--limit", type=int, default=20); cl.set_defaults(fn=cmd_checkpoint_list)
    cr = c.add_parser("render"); cr.add_argument("-o", "--output")
    cr.set_defaults(fn=cmd_checkpoint_render)

    cm = sub.add_parser("commerce", help="the money path").add_subparsers(
        dest="sub", required=True)
    cm.add_parser("status", help="offers, orders, and the numbers that must be zero"
                  ).set_defaults(fn=cmd_commerce_status)
    co = cm.add_parser("orders"); co.add_argument("--last", type=int, default=10)
    co.set_defaults(fn=cmd_commerce_orders)
    cm.add_parser("contract-test", help="probe + contract test the payment provider"
                  ).set_defaults(fn=cmd_commerce_contract_test)
    cm.add_parser("sweep", help="re-verify artifacts behind live tokens"
                  ).set_defaults(fn=cmd_commerce_sweep)
    tl = cm.add_parser("test-ladder", help="create a real low-value ladder for HT-005 verification")
    tl.add_argument("--base-minor", type=int, default=100,
                    help="roadmap anchor in MINOR units (default 100 = 1.00)")
    tl.add_argument("--label", default="v1")
    tl.set_defaults(fn=cmd_commerce_test_ladder)
    pb = cm.add_parser("publish", help="make a solution's ladder purchasable")
    pb.add_argument("solution_id")
    pb.set_defaults(fn=cmd_commerce_publish)

    tk = sub.add_parser("tasks", help="the human-task queue").add_subparsers(
        dest="sub", required=True)
    tk.add_parser("list", help="open tasks").set_defaults(fn=cmd_tasks_list)
    ts = tk.add_parser("show"); ts.add_argument("ref"); ts.set_defaults(fn=cmd_tasks_show)
    tr = tk.add_parser("reply", help="answer a task WITHOUT Telegram (the C7 fallback)")
    tr.add_argument("ref"); tr.add_argument("text", help="the reply, or '-' to read stdin")
    tr.set_defaults(fn=cmd_tasks_reply)
    tk.add_parser("expire", help="expire overdue tasks and announce them"
                  ).set_defaults(fn=cmd_tasks_expire)

    mk = sub.add_parser("market", help="phase F — positioning, copy, sales page"
                        ).add_subparsers(dest="sub", required=True)
    ms = mk.add_parser("show", help="copy blocks, coverage and page state (free)")
    ms.add_argument("need_id", type=int); ms.set_defaults(fn=cmd_market_show)
    mp = mk.add_parser("position", help="F1 — positioning from the buyer's own words")
    mp.add_argument("need_id", type=int); mp.set_defaults(fn=cmd_market_position)
    mc = mk.add_parser("copy", help="F2 — per-tier copy (SPENDS BUDGET)")
    mc.add_argument("need_id", type=int); mc.set_defaults(fn=cmd_market_copy)
    mg = mk.add_parser("page", help="F5 — render the sales page (free)")
    mg.add_argument("need_id", type=int); mg.set_defaults(fn=cmd_market_page)
    mr = mk.add_parser("recopy",
                       help="regenerate SOME copy blocks (SPENDS, ~$0.25/block)")
    mr.add_argument("need_id", type=int)
    mr.add_argument("--tier", choices=["roadmap", "instructions", "deployed"])
    mr.add_argument("--block", choices=["headline", "subhead", "benefits",
                                        "objections", "faq"])
    mr.add_argument("--below-floor", action="store_true", dest="below_floor",
                    help="only blocks under the coverage floor")
    mr.set_defaults(fn=cmd_market_recopy)
    ml = mk.add_parser("launch", help="F5b — plan outreach; NEVER sends")
    ml.add_argument("need_id", type=int); ml.set_defaults(fn=cmd_market_launch)

    ui = sub.add_parser("ui", help="render the operator dashboard to HTML")
    ui.add_argument("--out", default="-",
                    help="file to write, or '-' for stdout (default)")
    ui.set_defaults(fn=cmd_ui)

    tg = sub.add_parser("telegram", help="forum streams").add_subparsers(
        dest="sub", required=True)
    tg.add_parser("streams").set_defaults(fn=cmd_telegram_streams)
    tc = tg.add_parser("configure")
    tc.add_argument("stream"); tc.add_argument("--chat-id", type=int, required=True)
    tc.add_argument("--thread-id", type=int)
    tc.set_defaults(fn=cmd_telegram_configure)
    tg.add_parser("contract-test").set_defaults(fn=cmd_telegram_contract_test)
    tp = tg.add_parser("poll"); tp.add_argument("--timeout", type=int, default=5)
    tp.set_defaults(fn=cmd_telegram_poll)

    sub.add_parser("channels", help="buyer notification channels"
                   ).set_defaults(fn=cmd_channels)

    al = sub.add_parser("alerts", help="alert rules and their synthetic tests"
                        ).add_subparsers(dest="sub", required=True)
    ar = al.add_parser("render", help="print the Prometheus rule file to stdout")
    ar.add_argument("-o", "--output", default="-",
                    help="'-' (default) prints to stdout — redirect it on the HOST. "
                         "A path only works if that path is bind-mounted into "
                         "this container.")
    ar.set_defaults(fn=cmd_alerts_render)
    al.add_parser("synthetics", help="trip every rule and assert it fired"
                  ).set_defaults(fn=cmd_alerts_synthetics)
    al.add_parser("status", help="when each rule was last verified"
                  ).set_defaults(fn=cmd_alerts_status)

    sch = sub.add_parser("scheduler", help="autonomous job scheduler"
                         ).add_subparsers(dest="sub", required=True)
    sch.add_parser("tick", help="run every due job_registry job once "
                                "(the host timer calls this)"
                   ).set_defaults(fn=cmd_scheduler_tick)
    sch.add_parser("status", help="job freshness and what the next tick would run"
                   ).set_defaults(fn=cmd_scheduler_status)

    dv = sub.add_parser("discover", help="the discovery funnel").add_subparsers(
        dest="sub", required=True)
    dv.add_parser("run", help="run phase A end to end").set_defaults(fn=cmd_discover_run)
    dv.add_parser("census", help="which gate blocks what").set_defaults(fn=cmd_discover_census)
    dr = dv.add_parser("replay", help="what WOULD promote at other thresholds")
    dr.add_argument("--override", action="append", metavar="gate=value")
    dr.set_defaults(fn=cmd_discover_replay)
    dn = dv.add_parser("needs"); dn.add_argument("--limit", type=int, default=10)
    dn.set_defaults(fn=cmd_discover_needs)

    rs = sub.add_parser("research", help="phase B — evidence and grounding").add_subparsers(
        dest="sub", required=True)
    for name, fn, helptext in (
            ("run", cmd_research_run, "run phase B for one need"),
            ("dossier", cmd_research_dossier, "show the research dossier"),
            ("evidence", cmd_research_evidence, "list captured evidence"),
            ("verify", cmd_research_verify, "re-fetch every cited URL"),
            ("claims", cmd_research_claims, "list claims with their citations")):
        sp = rs.add_parser(name, help=helptext)
        sp.add_argument("need_id", type=int)
        sp.set_defaults(fn=fn)
    rc = rs.add_parser("check",
                       help="fact-check claims no artifact cites yet (SPENDS)")
    rc.add_argument("need_id", type=int)
    rc.add_argument("--all", action="store_true",
                    help="re-check every claim, not only unverified ones")
    rc.set_defaults(fn=cmd_research_check)
    rl = rs.add_parser("solution",
                       help="capture REMEDY evidence + supporting claims (SPENDS)")
    rl.add_argument("need_id", type=int)
    rl.add_argument("--queries", type=int, default=6)
    rl.add_argument("--limit", type=int, default=8)
    rl.set_defaults(fn=cmd_research_solution)

    fg = sub.add_parser("forge", help="phases C/D/E — the three tier artifacts"
                        ).add_subparsers(dest="sub", required=True)
    fr = fg.add_parser("run", help="generate + package + verify (SPENDS BUDGET)")
    fr.add_argument("need_id", type=int)
    fr.set_defaults(fn=cmd_forge_run)
    fx = fg.add_parser("repair",
                       help="regenerate ONE section (one LLM call, not a rebuild)")
    fx.add_argument("need_id", type=int)
    fx.add_argument("tier", choices=["roadmap", "instructions", "deployed"])
    fx.add_argument("section", help="section key, e.g. estimate, audience, risks")
    fx.add_argument("--brief", default="",
                    help="extra instruction, e.g. what the last attempt missed")
    fx.set_defaults(fn=cmd_forge_repair)
    fv = fg.add_parser("reverify",
                       help="re-package + re-verify from saved drafts (no spend)")
    fv.add_argument("need_id", type=int)
    fv.set_defaults(fn=cmd_forge_reverify)
    fa = fg.add_parser("artifacts"); fa.add_argument("need_id", type=int)
    fa.set_defaults(fn=cmd_forge_artifacts)
    fs = fg.add_parser("show"); fs.add_argument("need_id", type=int)
    fs.add_argument("tier", choices=["roadmap", "instructions", "deployed"])
    fs.add_argument("--chars", type=int, default=3000)
    fs.set_defaults(fn=cmd_forge_show)

    sub.add_parser("steps", help="registered steps and their contracts").set_defaults(fn=cmd_steps)
    cn = sub.add_parser("connectors", help="connector health")
    cnsub = cn.add_subparsers(dest="sub")
    cn.set_defaults(fn=cmd_connectors)          # bare `jpd connectors` still lists
    cnsub.add_parser("list").set_defaults(fn=cmd_connectors)
    ck = cnsub.add_parser("check", help="probe + contract test (drives dormancy)")
    ck.add_argument("name", nargs="?"); ck.set_defaults(fn=cmd_connectors_check)
    hv = cnsub.add_parser("harvest", help="call live connectors and record yield")
    hv.add_argument("name", nargs="?"); hv.add_argument("--limit", type=int, default=25)
    hv.set_defaults(fn=cmd_connectors_harvest)
    cnsub.add_parser("orphans", help="rows without code, and code without rows"
                     ).set_defaults(fn=cmd_connectors_orphans)
    sub.add_parser("doctor", help="preflight checks").set_defaults(fn=cmd_doctor)

    k = sub.add_parser("kill", help="stop a run (clears its lease)")
    k.add_argument("run", type=int); k.set_defaults(fn=cmd_kill)

    return p


async def _run(args) -> int:
    # Register the pipeline steps. The CLI is a SEPARATE PROCESS from the
    # service, so without this `jpd steps` reported "no steps registered" while
    # six were live in core — the CLI was telling the truth about its own
    # process and a lie about the system.
    try:
        from .discovery import steps as _discovery_steps
        from .research import steps as _research_steps
        from .forge import steps as _forge_steps
        from .market import steps as _market_steps
        _discovery_steps.register()
        _research_steps.register()
        _forge_steps.register()
        _market_steps.register()
    except Exception as e:                                       # noqa: BLE001
        print(f"{YEL}warning{RST}: could not register pipeline steps: {e}",
              file=sys.stderr)
    try:
        return await args.fn(args)
    finally:
        await db.close()


def _logs_to_stderr() -> None:
    """Structlog writes to STDOUT by default. Move it to stderr.

    🔴 Nothing ever called `structlog.configure()`, so every log line went to
    stdout alongside real command output. That makes `jpd <cmd> > file` produce
    a corrupt file: `jpd ui > dash.html` wrote `discovery.steps_registered`
    ahead of the doctype, because the registry logs at import time — before the
    command runs, so no amount of care inside the command can prevent it.

    Logs are diagnostics and belong on stderr; stdout is the artifact. This is
    set in main() only, so it applies to the `jpd` BINARY and not to tests that
    call the cmd_* functions directly and assert on captured stdout.
    """
    import structlog
    structlog.configure(
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))


def main(argv: list[str] | None = None) -> int:
    _logs_to_stderr()
    args = build_parser().parse_args(argv)
    if getattr(args, "dsn", None):
        os.environ["JPD_PG_DSN"] = args.dsn
    try:
        return asyncio.run(_run(args))
    except db.MigrationDrift as e:
        print(f"{RED}MIGRATION DRIFT{RST} {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as e:                                       # noqa: BLE001
        print(f"{RED}{type(e).__name__}{RST}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
