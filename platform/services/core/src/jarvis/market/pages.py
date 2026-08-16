"""F5 — the self-hosted sales page: three offers, one page, one checkout.

Self-hosted because GHL cannot create landing pages via API (verified), and
because a page we render is a page we can content-address — the sha is of the
bytes on disk, so "the page that was reviewed" and "the page that shipped" are
the same object or provably are not. That is the same guarantee the artifacts
get, and for the same reason.

🔴 A PAGE IS NOT PUBLISHABLE BY DEFAULT.

`publishable` requires, per tier on the page, a LIVE offer with a real checkout
url, and citation coverage at or above the floor. A sales page whose Buy button
goes nowhere is worse than no page: it burns the launch audience once and they
do not come back. Pimlico shipped delivery tokens pointing at files that did not
exist; this is the same failure one step earlier in the funnel.
"""
from __future__ import annotations

import hashlib
import html
import os
from pathlib import Path
from typing import Any, Optional

import structlog

from .. import db
from ..forge.build import find_placeholders
from ..forge.verify import citation_coverage
from .copy import BLOCKS, COVERAGE_FLOOR

log = structlog.get_logger("market.pages")

PAGE_DIR = Path(os.environ.get("JPD_ARTIFACT_DIR", "/app/data/artifacts")) / "pages"
TIER_ORDER = ("roadmap", "instructions", "deployed")


def _esc(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _md_inline(s: str) -> str:
    """The narrow slice of markdown the copy blocks actually produce.

    Deliberately not a markdown library: the input is our own generated text,
    the output is a public page, and a full renderer would happily emit raw HTML
    from model output straight onto it. Everything is escaped FIRST and only
    these four constructs are then re-introduced.
    """
    import re
    out = _esc(s)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", out)
    out = re.sub(r"`([^`]+?)`", r"<code>\1</code>", out)
    # Citations become visible superscripts — a promise the reader can follow.
    out = re.sub(r"\[claim (\d+)\]", r'<sup class="cite">\1</sup>', out)
    return out


def _block_html(body: str) -> str:
    lines = [ln.rstrip() for ln in (body or "").splitlines() if ln.strip()]
    out, in_list = [], False
    for ln in lines:
        if ln.lstrip().startswith(("- ", "* ")):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_md_inline(ln.lstrip()[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if ln.startswith("#"):
            out.append(f"<h3>{_md_inline(ln.lstrip('# '))}</h3>")
        else:
            out.append(f"<p>{_md_inline(ln)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


async def gather(need_id: int) -> dict[str, Any]:
    need = await db.fetchrow("SELECT id, title FROM needs WHERE id=$1", need_id)
    pos = await db.fetchrow(
        "SELECT pain_phrase, audience, promise, proof FROM positioning WHERE need_id=$1",
        need_id)
    blocks = await db.fetch(
        "SELECT tier, block, body, citation_pct FROM copy_blocks WHERE need_id=$1",
        need_id)
    offers = await db.fetch(
        """
        SELECT o.tier, o.price_minor, o.currency, o.checkout_url, o.live
          FROM offers o
          JOIN solutions s ON s.id = o.solution_id
         WHERE s.need_id = $1
        """, need_id)
    by_tier: dict[str, dict] = {}
    for b in blocks:
        by_tier.setdefault(b["tier"], {})[b["block"]] = dict(b)
    return {"need": dict(need) if need else {}, "pos": dict(pos) if pos else {},
            "blocks": by_tier,
            "offers": {o["tier"]: dict(o) for o in offers}}


def render(data: dict[str, Any]) -> str:
    need, pos = data["need"], data["pos"]
    blocks, offers = data["blocks"], data["offers"]
    tiers = [t for t in TIER_ORDER if t in blocks]

    tabs = "".join(
        f'<button class="tab" data-tier="{_esc(t)}" '
        f'{"aria-selected=true" if i == 0 else "aria-selected=false"}>'
        f'{_esc(t.title())}</button>' for i, t in enumerate(tiers))

    panels = []
    for i, t in enumerate(tiers):
        b = blocks[t]
        o = offers.get(t) or {}
        price = (f'{(o.get("price_minor") or 0) / 100:,.2f} '
                 f'{o.get("currency") or ""}').strip()
        url = o.get("checkout_url") or ""
        # No live offer → no button. A Buy button that goes nowhere burns the
        # launch audience once, and they do not come back.
        cta = (f'<a class="buy" href="{_esc(url)}">Buy the {_esc(t.title())} · '
               f'{_esc(price)}</a>' if (url and o.get("live"))
               else '<p class="nocta">Not yet available for purchase.</p>')
        body = "".join(
            f'<section class="blk blk-{k}">{_block_html(b[k]["body"])}</section>'
            for k in BLOCKS if k in b)
        panels.append(
            f'<div class="panel" data-tier="{_esc(t)}"'
            f'{"" if i == 0 else " hidden"}>{body}{cta}</div>')

    title = f'{need.get("title", "Solution")}'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title>
<style>
:root{{color-scheme:light;--bg:#fbfbf9;--fg:#0f1113;--fg2:#4a4f52;--rule:#e2e4e0;
 --accent:#2a78d6;--card:#fff}}
@media (prefers-color-scheme:dark){{:root{{--bg:#101214;--fg:#f2f3f1;--fg2:#b3b8ba;
 --rule:#282d30;--accent:#3987e5;--card:#171a1c}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);
 font:17px/1.65 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
main{{max-width:760px;margin:0 auto;padding:56px 22px 96px}}
.pain{{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--fg2)}}
h1{{font-size:clamp(30px,5vw,44px);line-height:1.1;margin:12px 0 10px;
 letter-spacing:-.02em;text-wrap:balance}}
.promise{{font-size:19px;color:var(--fg2);margin:0 0 8px}}
.proof{{font-size:15px;color:var(--fg2);border-left:3px solid var(--accent);
 padding-left:14px;margin:20px 0 32px}}
.tabs{{display:flex;gap:8px;border-bottom:1px solid var(--rule);margin-bottom:26px;
 flex-wrap:wrap}}
.tab{{background:none;border:0;border-bottom:2px solid transparent;padding:10px 4px;
 font:inherit;font-size:15px;font-weight:600;color:var(--fg2);cursor:pointer}}
.tab[aria-selected=true]{{color:var(--fg);border-bottom-color:var(--accent)}}
.tab:focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}
.blk{{margin:0 0 26px}} .blk h3{{font-size:17px;margin:22px 0 6px}}
ul{{padding-left:20px}} li{{margin:6px 0}}
sup.cite{{color:var(--accent);font-size:11px;font-weight:700;margin-left:2px}}
.buy{{display:inline-block;background:var(--accent);color:#fff;text-decoration:none;
 font-weight:650;padding:14px 26px;border-radius:8px;margin-top:10px}}
.nocta{{color:var(--fg2);font-style:italic}}
footer{{border-top:1px solid var(--rule);margin-top:40px;padding-top:18px;
 font-size:13px;color:var(--fg2)}}
@media (prefers-reduced-motion:reduce){{*{{transition:none!important}}}}
</style></head>
<body><main>
  <p class="pain">{_esc(pos.get('pain_phrase',''))}</p>
  <h1>{_esc(title)}</h1>
  <p class="promise">{_esc(pos.get('promise',''))}</p>
  <p class="proof">{_md_inline(pos.get('proof',''))}</p>
  <div class="tabs" role="tablist">{tabs}</div>
  {"".join(panels)}
  <footer>Every superscript is a citation to a source that was fetched, hashed
  and checked at research time.</footer>
</main>
<script>
document.querySelectorAll('.tab').forEach(function(t){{
  t.addEventListener('click', function(){{
    var tier = t.dataset.tier;
    document.querySelectorAll('.tab').forEach(function(x){{
      x.setAttribute('aria-selected', String(x === t)); }});
    document.querySelectorAll('.panel').forEach(function(p){{
      p.hidden = (p.dataset.tier !== tier); }});
  }});
}});
</script></body></html>"""


def page_state(data: dict[str, Any]) -> dict[str, Any]:
    """The publish decision, computed from `data` alone.

    Pure on purpose. This used to live inline in `build_page`, which writes to
    the database, so the rule that decides whether a page may be shown to
    buyers could not be tested without one — and it was wrong in two ways
    nobody noticed until a page was read by hand.
    """
    tiers = [t for t in TIER_ORDER if t in data["blocks"]]
    # Coverage of the page as rendered, not of the blocks in isolation.
    pct = citation_coverage(
        "\n".join(b["body"] for tb in data["blocks"].values()
                  for b in tb.values()))["coverage_pct"]
    sellable = [t for t in tiers
                if (data["offers"].get(t) or {}).get("live")
                and (data["offers"].get(t) or {}).get("checkout_url")]

    # 🔴 UNFINISHED WORK BLOCKS PUBLICATION. Added 2026-08-09.
    #
    # `find_placeholders` was already computed per block by `market.copy` — and
    # then thrown away. `store_block` does not persist it and nothing gated on
    # it, so it was a line the CLI printed once at generation time and never
    # again. A page was marked PUBLISHABLE carrying nine author notes,
    # including two `[NEEDS PRICING]` on a page whose whole job is to state a
    # price.
    #
    # An artifact carrying `[insert vendor name]` is withheld from sale. The
    # sales page is the FIRST thing a buyer reads, and it had no such rule.
    marks = sorted({m for tb in data["blocks"].values() for b in tb.values()
                    for m in find_placeholders(b["body"])})

    blockers: list[str] = []
    if not tiers:
        blockers.append("no copy blocks")
    if not sellable:
        blockers.append("no tier has a live offer")
    elif len(sellable) != len(tiers):
        missing = [t for t in tiers if t not in sellable]
        blockers.append(f"no live offer for {', '.join(missing)}")
    if pct < COVERAGE_FLOOR:
        blockers.append(f"citation coverage {pct:.1f}% is below the "
                        f"{COVERAGE_FLOOR:.0f}% floor")
    if marks:
        blockers.append("unfinished work: " + ", ".join(marks))

    return {"tiers": tiers, "sellable": sellable, "citation_pct": pct,
            "placeholders": marks, "blockers": blockers,
            "publishable": not blockers}


async def build_page(need_id: int, run_id: Optional[int] = None) -> dict[str, Any]:
    data = await gather(need_id)
    if not data["blocks"]:
        raise ValueError("no copy blocks — run market.copy first")

    html_text = render(data)
    raw = html_text.encode()
    sha = hashlib.sha256(raw).hexdigest()
    PAGE_DIR.mkdir(parents=True, exist_ok=True)
    path = PAGE_DIR / f"need-{need_id}-{sha[:12]}.html"
    path.write_bytes(raw)

    st = page_state(data)
    tiers, sellable = st["tiers"], st["sellable"]
    pct, marks, publishable = (st["citation_pct"], st["placeholders"],
                               st["publishable"])

    await db.execute(
        """
        INSERT INTO sales_pages (need_id, sha256, bytes, storage_uri, tiers,
                                 citation_pct, publishable, run_id)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        ON CONFLICT (need_id) DO UPDATE SET
          sha256=EXCLUDED.sha256, bytes=EXCLUDED.bytes,
          storage_uri=EXCLUDED.storage_uri, tiers=EXCLUDED.tiers,
          citation_pct=EXCLUDED.citation_pct, publishable=EXCLUDED.publishable,
          run_id=EXCLUDED.run_id, published_at=NULL
        """,
        need_id, sha, len(raw), f"file://{path}", len(tiers), pct,
        publishable, run_id)

    log.info("market.page_built", need_id=need_id, tiers=len(tiers),
             sellable=len(sellable), citation_pct=pct, publishable=publishable,
             placeholders=marks, path=str(path))
    return {"need_id": need_id, "path": str(path), "sha256": sha,
            "bytes": len(raw), "tiers": len(tiers), "sellable": len(sellable),
            "citation_pct": pct, "publishable": publishable,
            "placeholders": marks, "blockers": st["blockers"]}
