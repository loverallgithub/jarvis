"""The operator UI — one page that shows every aspect of the platform.

WHY THIS EXISTS
───────────────
Until now the only way to see system state was `jpd` on the box. That is fine
for a build session and useless from a phone, which is where this operator
actually is. Telegram carries *events* (a task, a decision, an alert); it is a
poor place to read *state*, because state is a table and Telegram is a stream.

So: events go to Telegram, state lives here, and both read the same database.

WHAT IT DELIBERATELY DOES NOT DO
────────────────────────────────
No writes. Every query below is a SELECT. A dashboard that can change things is
a dashboard that can change things by accident, and the mutating surface is
already covered — `jpd` on the box, and the safe subset in Telegram commands.

It is also SERVER-RENDERED with no external assets: no CDN, no fonts, no chart
library. The console must be readable when the network it would fetch from is
the thing that is broken.

COLOURS
───────
Status colours are the fixed status palette (good/warning/serious/critical) and
are always paired with a text label — never colour alone. Series colours are the
validated categorical slots, assigned in fixed order.
"""
from __future__ import annotations

import html
import os
from datetime import datetime, timezone
from typing import Any, Optional

from .. import db
from .telegram import STREAMS

# ── palette ────────────────────────────────────────────────────────────────
# Status palette — fixed, never themed, always paired with a label.
GOOD, WARNING, SERIOUS, CRITICAL = "#0ca30c", "#fab219", "#ec835a", "#d03b3b"
# Categorical slots, fixed order — referenced as CSS custom properties so the
# DARK steps are their own selected values, not an automatic flip of the light
# ones. Light #2a78d6/#eb6834/#1baf7a and dark #3987e5/#d95926/#199e70 were each
# validated as a set against their own surface (all checks pass in both modes;
# light aqua sits at 2.74:1, which the relief rule covers — every chart carries a
# visible caption and a table view).
S1, S2, S3 = "var(--s1)", "var(--s2)", "var(--s3)"

TREND_DAYS = 14


def _esc(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _pct(n: int, d: int) -> float:
    return (n / d * 100.0) if d else 0.0


# ── data ───────────────────────────────────────────────────────────────────
async def _scalar(q: str, *a: Any) -> int:
    return int(await db.fetchval(q, *a) or 0)


async def _series(table: str, col: str) -> list[dict]:
    """Daily counts for the trend window, zero-filled.

    Zero-filled on purpose: a gap day drawn as "no point" reads as missing data,
    while a gap day drawn at zero reads as "nothing happened", which is what it
    means. The two are not the same and the chart must not blur them.
    """
    rows = await db.fetch(
        f"""
        SELECT d::date AS day, count({table}.id) AS n
          FROM generate_series(now()::date - ($1::int - 1), now()::date,
                               interval '1 day') AS d
          LEFT JOIN {table} ON {table}.{col} >= d
                           AND {table}.{col} < d + interval '1 day'
         GROUP BY d ORDER BY d
        """, TREND_DAYS)
    return [{"day": str(r["day"]), "n": int(r["n"])} for r in rows]


async def snapshot() -> dict[str, Any]:
    """Everything the page shows, in one pass. Read-only throughout."""
    now = datetime.now(timezone.utc)

    connectors = [dict(r) for r in await db.fetch(
        "SELECT connector, kind, state, fail_streak, zero_yield_streak, "
        "       last_probe_at, updated_at "
        "  FROM connector_health ORDER BY state DESC, connector")]

    streams = [dict(r) for r in await db.fetch(
        "SELECT stream, chat_id, thread_id, enabled, purpose, updated_at "
        "  FROM telegram_streams ORDER BY stream")]

    tasks = [dict(r) for r in await db.fetch(
        "SELECT ref, type, title, status, stream, created_at, resolved_at, "
        "       telegram_thread_id, reply_attempts "
        "  FROM human_tasks ORDER BY created_at DESC LIMIT 25")]

    needs = [dict(r) for r in await db.fetch(
        "SELECT id, title, audience, status, score, frequency, severity, "
        "       distinct_voices, created_at "
        "  FROM needs ORDER BY score DESC NULLS LAST, id")]

    # One row per PRODUCT — the artifact plus every KPI that decides whether it
    # can be sold, so the page answers "what is blocking this" without a join in
    # the reader's head.
    artifacts = [dict(r) for r in await db.fetch(
        """
        SELECT a.id, a.need_id, a.tier, a.words, a.sections, a.bytes,
               a.structural_ok, a.factual_ok, a.offerable, a.sha256,
               a.created_at, a.storage_uri, a.verify_detail,
               n.title AS need_title, n.audience,
               (SELECT count(*) FROM artifact_claims ac
                 WHERE ac.artifact_id = a.id) AS claims_cited,
               (SELECT count(*) FROM artifact_claims ac JOIN claims c
                       ON c.id = ac.claim_id
                 WHERE ac.artifact_id = a.id AND c.supported) AS claims_supported,
               (SELECT count(*) FROM artifact_claims ac JOIN claims c
                       ON c.id = ac.claim_id
                 WHERE ac.artifact_id = a.id AND c.supported IS FALSE)
                 AS claims_unsupported,
               (SELECT count(*) FROM acceptance_tests t
                 WHERE t.need_id = a.need_id AND t.tier = a.tier) AS tests_total,
               (SELECT count(*) FROM acceptance_tests t
                 WHERE t.need_id = a.need_id AND t.tier = a.tier
                   AND t.last_result = 'pass') AS tests_passed,
               (SELECT count(DISTINCT c.evidence_id) FROM artifact_claims ac
                       JOIN claims c ON c.id = ac.claim_id
                 WHERE ac.artifact_id = a.id AND c.evidence_id IS NOT NULL)
                 AS sources_cited,
               o.price_minor, o.currency, o.live AS offer_live, o.checkout_url
          FROM artifacts a
          LEFT JOIN needs n ON n.id = a.need_id
          LEFT JOIN offers o ON o.tier = a.tier AND o.solution_id IN (
                    SELECT id FROM solutions WHERE need_id = a.need_id)
         ORDER BY a.need_id, a.id
        """)]

    pricing = {r["tier"]: dict(r) for r in await db.fetch(
        "SELECT tier, ratio_min, ratio_max, rationale FROM pricing_policy")}

    offers = [dict(r) for r in await db.fetch(
        "SELECT id, tier, currency, price_minor, live, provider, created_at "
        "  FROM offers ORDER BY id")]

    claims = dict(await db.fetchrow(
        "SELECT count(*) AS total, "
        "       count(*) FILTER (WHERE supported) AS supported, "
        "       count(*) FILTER (WHERE supported IS FALSE) AS unsupported, "
        "       count(*) FILTER (WHERE evidence_id IS NULL) AS uncited "
        "  FROM claims WHERE id IN (SELECT claim_id FROM artifact_claims)") or {})

    evidence = dict(await db.fetchrow(
        "SELECT count(*) AS total, "
        "       count(*) FILTER (WHERE substantive) AS substantive, "
        "       count(*) FILTER (WHERE live_at_capture AND substantive) AS usable "
        "  FROM evidence") or {})

    runs = [dict(r) for r in await db.fetch(
        "SELECT id, need_id, phase, status, cost_usd, started_at, ended_at "
        "  FROM runs ORDER BY id DESC LIMIT 12")]

    gates = [dict(r) for r in await db.fetch(
        "SELECT gate, count(*) AS n, count(*) FILTER (WHERE passed) AS passed "
        "  FROM gate_evaluations GROUP BY gate ORDER BY gate")]

    sources = [dict(r) for r in await db.fetch(
        "SELECT name, source_type, enabled, health_state, fail_streak, last_yield_at "
        "  FROM sources ORDER BY health_state DESC, name")]

    checkpoint = await db.fetchrow(
        "SELECT id, label, phase, reason, created_at, resumable_from "
        "  FROM checkpoints ORDER BY id DESC LIMIT 1")

    poller = await db.fetchrow(
        "SELECT job_name, last_success_at, now() - last_success_at AS age "
        "  FROM job_registry WHERE job_name = 'console.poll_replies'")

    replies = [dict(r) for r in await db.fetch(
        "SELECT update_id, thread_id, accepted, reject_reason, matched_task_id "
        "  FROM telegram_replies ORDER BY update_id DESC LIMIT 10")]

    acceptance = [dict(r) for r in await db.fetch(
        "SELECT tier, count(*) AS n FROM acceptance_tests GROUP BY tier ORDER BY tier")]

    funnel = {
        "signals": await _scalar("SELECT count(*) FROM signals"),
        "clusters": await _scalar("SELECT count(*) FROM clusters"),
        "needs": len(needs),
        "artifacts": len(artifacts),
        "offers": len(offers),
    }

    return {
        "now": now,
        "service": os.environ.get("JPD_SERVICE", "jarvis-console"),
        "version": os.environ.get("JPD_VERSION", "unknown"),
        "env": os.environ.get("JPD_ENV", "production"),
        "connectors": connectors, "streams": streams, "tasks": tasks,
        "needs": needs, "artifacts": artifacts, "offers": offers,
        "claims": claims, "evidence": evidence, "runs": runs, "gates": gates,
        "pricing": pricing,
        "sources": sources, "checkpoint": dict(checkpoint) if checkpoint else None,
        "poller": dict(poller) if poller else None, "replies": replies,
        "acceptance": acceptance, "funnel": funnel,
        "trend_signals": await _series("signals", "observed_at"),
        "trend_evidence": await _series("evidence", "fetched_at"),
        "trend_runs": await _series("runs", "started_at"),
    }


# ── marks ──────────────────────────────────────────────────────────────────
def _line_chart(series: list[dict], colour: str, label: str) -> str:
    """One series, so no legend — the title names it (per the form rules).

    Thin 2px line, recessive baseline, hover target larger than the mark, and a
    <details> table underneath so the numbers are reachable without colour or
    pointer. No dual axis, no gradient fill, no value printed on every point.
    """
    if not series:
        return '<p class="muted">no data</p>'
    w, h, pad = 560, 120, 8
    vals = [p["n"] for p in series]
    hi = max(vals) or 1
    n = len(series)
    step = (w - pad * 2) / max(1, n - 1)

    def x(i: int) -> float:
        return pad + i * step

    def y(v: int) -> float:
        return pad + (h - pad * 2) * (1 - v / hi)

    pts = " ".join(f"{x(i):.1f},{y(p['n']):.1f}" for i, p in enumerate(series))
    dots = "".join(
        f'<g class="pt"><circle cx="{x(i):.1f}" cy="{y(p["n"]):.1f}" r="9" '
        f'fill="transparent"/><circle class="dot" cx="{x(i):.1f}" '
        f'cy="{y(p["n"]):.1f}" r="3" fill="{colour}"/>'
        f'<title>{_esc(p["day"])}: {p["n"]}</title></g>'
        for i, p in enumerate(series))
    rows = "".join(f"<tr><td>{_esc(p['day'])}</td><td>{p['n']}</td></tr>"
                   for p in series)
    return f"""
<figure class="chart">
  <figcaption>{_esc(label)} <span class="muted">· peak {hi}/day</span></figcaption>
  <svg viewBox="0 0 {w} {h}" role="img" aria-label="{_esc(label)}">
    <line x1="{pad}" y1="{h - pad}" x2="{w - pad}" y2="{h - pad}"
          stroke="var(--axis)" stroke-width="1"/>
    <polyline points="{pts}" fill="none" stroke="{colour}" stroke-width="2"
              stroke-linejoin="round" stroke-linecap="round"/>
    {dots}
  </svg>
  <details><summary>table</summary>
    <table class="mini"><thead><tr><th>day</th><th>count</th></tr></thead>
    <tbody>{rows}</tbody></table>
  </details>
</figure>"""


def _funnel(f: dict) -> str:
    """Magnitude across ordered stages → horizontal bars, one hue.

    Bars, not a tapered funnel graphic: a funnel shape encodes the number twice
    (width AND area) and the area is the lie. Each bar is labelled with its own
    value, so length is a comparison aid rather than the only channel.
    """
    stages = [("signals", f["signals"]), ("clusters", f["clusters"]),
              ("needs", f["needs"]), ("artifacts", f["artifacts"]),
              ("offers", f["offers"])]
    hi = max((v for _, v in stages), default=1) or 1
    # Ordinal ramp, light→dark, starting no lighter than step 250 on light.
    ramp = ["#86b6ef", "#5598e7", "#3987e5", "#256abf", "#184f95"]
    out = []
    for (name, v), c in zip(stages, ramp):
        pctw = max(0.6, _pct(v, hi))
        out.append(
            f'<div class="frow"><span class="fname">{_esc(name)}</span>'
            f'<span class="fbar"><span style="width:{pctw:.1f}%;background:{c}"></span></span>'
            f'<span class="fval">{v}</span></div>')
    return '<div class="funnel">' + "".join(out) + "</div>"


TIER_BLURB = {
    "roadmap": "What to build, in what order, at what cost — and why.",
    "instructions": "Roadmap + the full build manual. A competent operator "
                    "executes it without asking a question.",
    "deployed": "Instructions + built, configured, tested, handed over.",
}


def _blockers(a: dict) -> list[str]:
    """Why this product cannot be sold, in the buyer's terms.

    `offerable = structural AND factual` is the rule, but "factual_ok = false"
    tells an operator nothing actionable. These are the specific reasons, drawn
    from the same verify_detail the verifier wrote.
    """
    out: list[str] = []
    if a["claims_unsupported"]:
        out.append(f'{a["claims_unsupported"]} unsupported claim'
                   f'{"s" if a["claims_unsupported"] != 1 else ""}')
    detail = a.get("verify_detail")
    if isinstance(detail, str):
        try:
            import json
            detail = json.loads(detail)
        except Exception:                                        # noqa: BLE001
            detail = None
    if isinstance(detail, dict):
        for key, label in (("missing_sections", "missing section"),
                           ("thin_sections", "thin section"),
                           ("placeholders", "placeholder")):
            vals = detail.get(key) or []
            if vals:
                out.append(f'{label}{"s" if len(vals) != 1 else ""}: '
                           + ", ".join(str(v) for v in vals[:3]))
    if not a["structural_ok"] and not out:
        out.append("structural check failed")
    return out


def _product_card(a: dict, pricing: dict) -> str:
    tier = a["tier"]
    pol = pricing.get(tier) or {}
    ratio = ""
    if pol:
        # float() first: these are numeric/Decimal from postgres, and `:g` on a
        # Decimal formats as "1.00" rather than "1".
        lo, hi = float(pol.get("ratio_min") or 0), float(pol.get("ratio_max") or 0)
        ratio = f"{lo:g}×" if lo == hi else f"{lo:g}–{hi:g}×"

    price = "not priced"
    if a.get("price_minor"):
        price = f'{a["price_minor"] / 100:,.2f} {a.get("currency") or ""}'.strip()

    sell = (f'<span class="pill" style="--c:{GOOD}">OFFERABLE</span>'
            if a["offerable"] else
            f'<span class="pill" style="--c:{WARNING}">withheld</span>')

    supported = a["claims_supported"]
    cited = a["claims_cited"] or 0
    verified_pct = _pct(supported, cited)

    # Citation coverage from the stored verdict. Distinct from "claims
    # verified": that counts the claims the generator DID cite, this counts the
    # assertions it should have.
    detail = a.get("verify_detail")
    if isinstance(detail, str):
        try:
            import json
            detail = json.loads(detail)
        except Exception:                                        # noqa: BLE001
            detail = None
    covpct = (detail or {}).get("citation_pct") if isinstance(detail, dict) else None
    cov_txt = f'{covpct:.0f}%' if isinstance(covpct, (int, float)) else "—"

    kpis = [
        (f'{a["words"]:,}', "words"),
        (a["sections"], "sections"),
        (f'{supported}/{cited}', "claims verified"),
        (cov_txt, "cited assertions"),
        (a["sources_cited"], "sources"),
        (f'{a["tests_passed"]}/{a["tests_total"]}', "acceptance tests"),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><div class="kv">{_esc(v)}</div>'
        f'<div class="kl">{_esc(l)}</div></div>' for v, l in kpis)

    blockers = _blockers(a)
    block_html = ""
    if blockers:
        items = "".join(f"<li>{_esc(b)}</li>" for b in blockers)
        block_html = (f'<div class="blockers"><strong>Blocking sale:</strong>'
                      f'<ul>{items}</ul></div>')

    path = (a.get("storage_uri") or "").replace("file://", "")
    links = [f'<a href="/artifact/{a["id"]}">open product ↗</a>',
             f'<a href="/artifact/{a["id"]}?raw=1">markdown</a>']
    if a.get("checkout_url"):
        links.append(f'<a href="{_esc(a["checkout_url"])}">checkout ↗</a>')
    link_html = " · ".join(links)

    return f"""
<article class="product">
  <div class="phead">
    <div>
      <span class="ptier">{_esc(tier)}</span>
      {sell}
      <span class="muted"> · #{a["id"]} · {_esc(price)}{
        f" · {_esc(ratio)}" if ratio else ""}</span>
    </div>
    <div class="verifybar" title="{verified_pct:.0f}% of cited claims verified">
      <span style="width:{verified_pct:.0f}%"></span>
    </div>
  </div>
  <p class="pblurb">{_esc(TIER_BLURB.get(tier, ""))}</p>
  <div class="kpis">{kpi_html}</div>
  {block_html}
  <p class="plinks">{link_html}</p>
  <p class="ppath muted"><code>{_esc(path)}</code><br>
     <code>jpd forge show {a["need_id"]} {_esc(tier)}</code></p>
</article>"""


def _products(arts: list[dict], pricing: dict, needs: list[dict]) -> str:
    """Grouped by need — the three tiers of one need are ONE product family,
    a strict superset ladder, not three unrelated things."""
    if not arts:
        return '<p class="muted">no products built yet</p>'
    by_need: dict[int, list[dict]] = {}
    for a in arts:
        by_need.setdefault(a["need_id"], []).append(a)
    need_by_id = {n["id"]: n for n in needs}

    order = {"roadmap": 0, "instructions": 1, "deployed": 2}
    out = []
    for need_id, group in by_need.items():
        group.sort(key=lambda a: order.get(a["tier"], 9))
        n = need_by_id.get(need_id, {})
        sellable = sum(1 for a in group if a["offerable"])
        title = group[0].get("need_title") or f"need {need_id}"
        out.append(
            f'<div class="family"><div class="fhead">'
            f'<h3>{_esc(title)}</h3>'
            f'<span class="muted">need #{need_id}'
            + (f' · {_esc(n.get("audience"))}' if n.get("audience") else "")
            + f' · <strong>{sellable}/{len(group)} sellable</strong></span></div>'
            + '<div class="pgrid">'
            + "".join(_product_card(a, pricing) for a in group)
            + "</div></div>")
    return "".join(out)


def _state_pill(state: str) -> str:
    """Colour + word, never colour alone."""
    s = (state or "").lower()
    colour = {"live": GOOD, "dormant": WARNING, "degraded": SERIOUS,
              "failed": CRITICAL}.get(s, SERIOUS)
    return f'<span class="pill" style="--c:{colour}">{_esc(state or "?")}</span>'


def _bool_pill(ok: Optional[bool], yes: str, no: str) -> str:
    if ok is None:
        return f'<span class="pill" style="--c:{WARNING}">unknown</span>'
    c = GOOD if ok else CRITICAL
    return f'<span class="pill" style="--c:{c}">{_esc(yes if ok else no)}</span>'


def _tile(value: Any, label: str, sub: str = "", colour: str = "") -> str:
    style = f' style="color:{colour}"' if colour else ""
    return (f'<div class="tile"><div class="tval"{style}>{_esc(value)}</div>'
            f'<div class="tlab">{_esc(label)}</div>'
            f'<div class="tsub muted">{_esc(sub)}</div></div>')


def _ago(ts: Any, now: datetime) -> str:
    if not ts:
        return "never"
    try:
        d = now - ts
    except TypeError:
        return str(ts)
    s = int(d.total_seconds())
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


# ── page ───────────────────────────────────────────────────────────────────
CSS = """
:root{color-scheme:light;--plane:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;
 --ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--radius:10px;
 --s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--link:#2a78d6}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
 color-scheme:dark;--plane:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;
 --muted:#898781;--grid:#2c2c2a;--axis:#383835;
 --s1:#3987e5;--s2:#d95926;--s3:#199e70;--link:#3987e5}}
:root[data-theme=dark]{color-scheme:dark;--plane:#0d0d0d;--surface:#1a1a19;
 --ink:#fff;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;
 --s1:#3987e5;--s2:#d95926;--s3:#199e70;--link:#3987e5}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);
 font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{padding:20px 22px;border-bottom:1px solid var(--grid);
 display:flex;flex-wrap:wrap;gap:12px;align-items:baseline}
h1{font-size:17px;margin:0;letter-spacing:-.01em}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.07em;
 color:var(--ink2);margin:0 0 10px}
main{padding:18px 22px 60px;max-width:1400px;margin:0 auto}
.muted{color:var(--muted)}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
.card{background:var(--surface);border:1px solid var(--grid);
 border-radius:var(--radius);padding:14px 16px;min-width:0}
.card.wide{grid-column:1/-1}
.tiles{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
 margin-bottom:16px}
.tile{background:var(--surface);border:1px solid var(--grid);
 border-radius:var(--radius);padding:12px 14px}
.tval{font-size:26px;font-weight:650;letter-spacing:-.02em}
.tlab{font-size:12px;color:var(--ink2);margin-top:2px}
.tsub{font-size:11px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-weight:600;color:var(--ink2);font-size:11px;
 text-transform:uppercase;letter-spacing:.05em;padding:6px 8px 6px 0;
 border-bottom:1px solid var(--grid)}
td{padding:6px 8px 6px 0;border-bottom:1px solid var(--grid);
 vertical-align:top;color:var(--ink2)}
td.k{color:var(--ink);font-weight:550}
tr:last-child td{border-bottom:0}
.scroll{overflow-x:auto;max-width:100%}
.pill{display:inline-flex;align-items:center;gap:5px;font-size:11px;
 font-weight:600;color:var(--ink);padding:1px 8px;border-radius:99px;
 border:1px solid color-mix(in srgb,var(--c) 45%,transparent);
 background:color-mix(in srgb,var(--c) 14%,transparent)}
.pill::before{content:"";width:7px;height:7px;border-radius:99px;background:var(--c)}
.chart figcaption{font-size:12px;color:var(--ink2);margin-bottom:4px}
.chart svg{width:100%;height:auto;display:block}
.pt .dot{transition:r .1s}.pt:hover .dot{r:5}
.chart details{margin-top:6px}
.chart summary{font-size:11px;color:var(--muted);cursor:pointer}
table.mini{font-size:11px;margin-top:6px}
.funnel{display:flex;flex-direction:column;gap:7px}
.frow{display:grid;grid-template-columns:74px 1fr 52px;gap:10px;align-items:center}
.fname{font-size:12px;color:var(--ink2)}
.fbar{background:var(--grid);border-radius:4px;height:15px;overflow:hidden}
.fbar>span{display:block;height:100%;border-radius:4px}
.fval{text-align:right;font-weight:600;font-size:13px}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.family{margin-bottom:18px}
.fhead{display:flex;flex-wrap:wrap;gap:10px;align-items:baseline;
 padding-bottom:8px;border-bottom:1px solid var(--grid);margin-bottom:12px}
.fhead h3{font-size:15px;margin:0;letter-spacing:-.01em}
.pgrid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}
.product{border:1px solid var(--grid);border-radius:var(--radius);padding:12px 14px;
 background:var(--plane);min-width:0}
.phead{display:flex;flex-direction:column;gap:8px}
.ptier{font-weight:650;text-transform:capitalize;margin-right:6px}
.pblurb{font-size:12px;color:var(--ink2);margin:8px 0 10px}
.verifybar{background:var(--grid);border-radius:99px;height:5px;overflow:hidden}
.verifybar>span{display:block;height:100%;background:var(--s1);border-radius:99px}
.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px}
.kpi{background:var(--surface);border:1px solid var(--grid);border-radius:7px;
 padding:6px 8px}
.kv{font-size:15px;font-weight:650;letter-spacing:-.01em}
.kl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.blockers{border-left:3px solid #fab219;padding:6px 10px;margin:0 0 10px;
 background:color-mix(in srgb,#fab219 10%,transparent);border-radius:0 6px 6px 0}
.blockers strong{font-size:11px;text-transform:uppercase;letter-spacing:.05em}
.blockers ul{margin:4px 0 0;padding-left:16px;font-size:12px;color:var(--ink2)}
.plinks{font-size:12px;margin:0 0 6px}
.ppath{font-size:10px;margin:0;word-break:break-all;line-height:1.7}
a{color:var(--link)}
.note{font-size:12px;color:var(--muted);margin-top:8px}
"""


def _table(headers: list[str], rows: list[list[str]], empty: str = "none") -> str:
    if not rows:
        return f'<p class="muted">{_esc(empty)}</p>'
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"
                   for r in rows)
    return (f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


def render(d: dict[str, Any]) -> str:
    now = d["now"]
    conns = d["connectors"]
    live = sum(1 for c in conns if c["state"] == "live")
    open_tasks = sum(1 for t in d["tasks"] if t["status"] == "open")
    arts = d["artifacts"]
    offerable = sum(1 for a in arts if a["offerable"])
    streams_ok = sum(1 for s in d["streams"] if s["thread_id"] is not None)
    cl = d["claims"] or {}
    ev = d["evidence"] or {}

    poller = d["poller"] or {}
    age = poller.get("age")
    age_s = age.total_seconds() if age is not None else None
    poller_ok = age_s is not None and age_s < 180

    tiles = "".join([
        _tile(f'{live}/{len(conns)}', "connectors live",
              f"{len(conns) - live} dormant", GOOD if live else CRITICAL),
        _tile(open_tasks, "open human tasks",
              "queue is clear" if not open_tasks else "awaiting reply",
              GOOD if not open_tasks else WARNING),
        _tile(f'{streams_ok}/{len(d["streams"])}', "telegram streams",
              "HT-001 complete" if streams_ok == len(d["streams"]) else "incomplete",
              GOOD if streams_ok == len(d["streams"]) else CRITICAL),
        _tile(f'{offerable}/{len(arts)}', "artifacts offerable",
              "withheld until verified" if offerable < len(arts) else "all sellable",
              GOOD if offerable else WARNING),
        _tile(cl.get("unsupported", 0), "unsupported claims",
              f'{cl.get("supported", 0)} supported of {cl.get("total", 0)}',
              GOOD if not cl.get("unsupported") else CRITICAL),
        _tile(ev.get("usable", 0), "usable evidence",
              f'of {ev.get("total", 0)} captured', S1),
    ])

    conn_rows = [[
        f'<span class="k">{_esc(c["connector"])}</span>',
        _state_pill(c["state"]), _esc(c["kind"]),
        _esc(c["fail_streak"]), _esc(c["zero_yield_streak"]),
        _esc(_ago(c["last_probe_at"], now)),
    ] for c in conns]

    stream_rows = [[
        f'<span class="k">{_esc(s["stream"])}</span>',
        _bool_pill(s["thread_id"] is not None, "configured", "missing"),
        f'<code>{_esc(s["chat_id"])}</code>', f'<code>{_esc(s["thread_id"])}</code>',
        _esc(s["purpose"]),
    ] for s in d["streams"]]

    task_rows = [[
        f'<code class="k">{_esc(t["ref"])}</code>',
        _bool_pill(t["status"] != "open", t["status"], "open"),
        _esc(t["title"]), _esc(t["stream"]),
        _esc(_ago(t["created_at"], now)),
    ] for t in d["tasks"]]

    need_rows = [[
        f'<span class="k">{_esc(n["title"])}</span>',
        _esc(n["audience"]), _esc(n["status"]),
        _esc(round(n["score"], 3) if n["score"] is not None else "—"),
        _esc(n["distinct_voices"]),
    ] for n in d["needs"]]

    offer_rows = [[
        _esc(o["tier"]),
        _esc(f'{(o["price_minor"] or 0) / 100:.2f} {o["currency"] or ""}'),
        _bool_pill(o["live"], "live", "draft"), _esc(o["provider"]),
    ] for o in d["offers"]]

    gate_rows = [[
        f'<span class="k">{_esc(g["gate"])}</span>', _esc(g["passed"]),
        _esc(g["n"]), _esc(f'{_pct(g["passed"], g["n"]):.0f}%'),
    ] for g in d["gates"]]

    run_rows = [[
        f'<code class="k">#{_esc(r["id"])}</code>', _esc(r["phase"]),
        _bool_pill(r["status"] in ("succeeded", "ok"), r["status"], r["status"]),
        _esc(f'${float(r["cost_usd"] or 0):.2f}'),
        _esc(_ago(r["started_at"], now)),
    ] for r in d["runs"]]

    src_rows = [[
        f'<span class="k">{_esc(s["name"])}</span>', _state_pill(s["health_state"]),
        _esc(s["source_type"]), _esc(s["fail_streak"]),
        _esc(_ago(s["last_yield_at"], now)),
    ] for s in d["sources"]]

    reply_rows = [[
        f'<code>{_esc(r["update_id"])}</code>',
        _bool_pill(r["accepted"], "accepted", "rejected"),
        _esc(r["thread_id"]), _esc(r["reject_reason"] or "—"),
    ] for r in d["replies"]]

    ck = d["checkpoint"] or {}
    ck_html = (
        f'<p><code class="k">#{_esc(ck.get("id"))} {_esc(ck.get("label"))}</code> '
        f'<span class="muted">· {_esc(ck.get("phase"))} · '
        f'{_esc(_ago(ck.get("created_at"), now))}</span></p>'
        f'<p class="muted">resumable from '
        f'<code>{_esc(ck.get("resumable_from") or "—")}</code></p>'
        f'<details><summary>reason</summary><p>{_esc(ck.get("reason"))}</p></details>'
    ) if ck else '<p class="muted">no checkpoint</p>'

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>JPD · operator dashboard</title>
<style>{CSS}</style></head>
<body>
<header>
  <h1>JarvisProductDevelopment</h1>
  <span class="muted">{_esc(d["service"])} · {_esc(d["version"])} ·
    {_esc(d["env"])} · generated {_esc(now.strftime("%Y-%m-%d %H:%M UTC"))}</span>
</header>
<main>
  <div class="tiles">{tiles}</div>

  <div class="grid">
    <section class="card">
      <h2>Pipeline</h2>
      {_funnel(d["funnel"])}
      <p class="note">Each stage is a strict subset of the one before it.
        A stage at zero is a real stop, not a rendering gap.</p>
    </section>

    <section class="card">
      <h2>Telegram</h2>
      <p>{_bool_pill(poller_ok, "poller alive", "poller stale")}
         <span class="muted">last cycle
         {_esc(_ago(poller.get("last_success_at"), now))}</span></p>
      <p class="note">The poller logs nothing on idle cycles — silence is not
        death. This heartbeat is the liveness check.</p>
      {_table(["stream", "state", "chat", "thread", "purpose"], stream_rows)}
    </section>

    <section class="card wide">
      <h2>Products</h2>
      {_products(arts, d["pricing"], d["needs"])}
      <p class="note">The three tiers of a need are ONE product family — a
        strict superset ladder, so an upgrade delivers only the delta and a
        failed top tier still leaves two sellable products behind. A product is
        <strong>offerable</strong> only when it is both structurally complete
        and factually verified; withholding is the designed behaviour, not a
        fault. Price ratios come from <code>pricing_policy</code>; the 1×
        anchor is set per solution from observed willingness-to-pay.</p>
      <h2 style="margin-top:18px">Needs</h2>
      {_table(["need", "audience", "status", "score", "voices"], need_rows)}
      <h2 style="margin-top:16px">Offers</h2>
      {_table(["tier", "price", "state", "provider"], offer_rows,
              "no offers — the money path needs a store (HT-005)")}
    </section>

    <section class="card wide">
      <h2>Trends · last {TREND_DAYS} days</h2>
      <div class="grid">
        <div>{_line_chart(d["trend_signals"], S1, "Signals harvested")}</div>
        <div>{_line_chart(d["trend_evidence"], S2, "Evidence captured")}</div>
        <div>{_line_chart(d["trend_runs"], S3, "Runs started")}</div>
      </div>
    </section>

    <section class="card">
      <h2>Connectors · {live} live of {len(conns)}</h2>
      {_table(["connector", "state", "kind", "fails", "zero-yield", "probed"],
              conn_rows)}
    </section>

    <section class="card">
      <h2>Sources</h2>
      {_table(["source", "state", "type", "fails", "last yield"], src_rows)}
    </section>

    <section class="card">
      <h2>Human tasks</h2>
      {_table(["ref", "state", "title", "stream", "age"], task_rows,
              "no tasks yet")}
      <h2 style="margin-top:16px">Recent Telegram replies</h2>
      {_table(["update", "state", "thread", "reason"], reply_rows)}
    </section>

    <section class="card">
      <h2>Discovery gates</h2>
      {_table(["gate", "passed", "evaluated", "rate"], gate_rows)}
      <p class="note">Every gate must be enabled. A disabled gate was once a
        passed gate — <code>all([])</code> is <code>True</code>.</p>
    </section>

    <section class="card">
      <h2>Runs</h2>
      {_table(["run", "phase", "status", "cost", "started"], run_rows)}
    </section>

    <section class="card">
      <h2>Latest checkpoint</h2>
      {ck_html}
    </section>
  </div>
  <p class="note">Read-only. Every query on this page is a SELECT; nothing here
    can change state. Mutating operations live in <code>jpd</code> and in the
    safe Telegram command subset.</p>
</main></body></html>"""


async def page() -> str:
    return render(await snapshot())
