"""B1 — capture evidence: fetch, hash, record.

────────────────────────────────────────────────────────────────────────────
C4 LIVES HERE
────────────────────────────────────────────────────────────────────────────
Every row this module writes carries a URL, a **sha256 of the bytes actually
received**, the timestamp of the fetch, the HTTP status, and
`live_at_capture` — a boolean recording that the URL really resolved when we
looked, not that we intended to look.

Pimlico had no citation field anywhere and sold 27.5k-word products that were
pure model recall. The difference is not that JPD is more careful; it is that
`claims.evidence_id` is `NOT NULL` and the only way to get an evidence id is to
have fetched something.

────────────────────────────────────────────────────────────────────────────
WHY THERE IS NO you.com HERE
────────────────────────────────────────────────────────────────────────────
The design specifies you.com Research at $0.012/call. No such key exists on
this host — the Pimlico stack env, the running containers and both .env backups
were all checked. Rather than block the phase on a credential, search runs
through DuckDuckGo lite, verified 200 from this VPS. It sits behind the same
connector contract, so swapping in you.com later is one class and a registry row.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional
from urllib.parse import quote_plus, urlparse, parse_qs, unquote

import httpx
import structlog

from .. import db
from ..connectors.base import ConnectorError, ProbeResult, TestResult

log = structlog.get_logger("research.evidence")

UA = ("Mozilla/5.0 (compatible; JarvisProductDevelopment/0.1; "
      "+admin@pimlicoservices.com)")
DEFAULT_TIMEOUT = 20
# 🔴 Both caps were measured, not guessed (2026-08-08, need 13).
#
# MAX_BYTES was 400_000. Real pages in this domain: ramp.com 1,448,182 bytes,
# mhcautomation.com 753,559, highradius.com 356,039. At 400 KB the mhc article
# was ENTIRELY outside the capture — the cut landed inside a huge inlined
# stylesheet, leaving a 67-character body that the substantive gate rightly
# rejected. Raising the cap to 2 MB yields 61,874 characters of real article
# from that same page.
#
# BODY_CHARS was 8_000, applied to the STRIPPED text. That silently discarded
# most of what was captured even when the fetch succeeded: ramp strips to 42,503
# characters and only the first 8,000 were kept. The fact-checker searches this
# field, so anything cut here is invisible to verification no matter how good
# the excerpt selection is.
#
# Cost of both: bytes in postgres, on a table with tens of rows. That is a much
# cheaper thing to spend than a claim that cannot be verified because the
# sentence supporting it was truncated away.
MAX_BYTES = 2_000_000
BODY_CHARS = 60_000


async def params() -> dict[str, str]:
    try:
        rows = await db.fetch("SELECT param, value FROM research_params")
        return {r["param"]: r["value"] for r in rows}
    except Exception:                                            # noqa: BLE001
        return {}


# 🔴 POSTGRES text CANNOT STORE A NUL BYTE, and a fetched page can contain one.
#
# Observed 2026-08-09 on the first solution-research capture: the insert died
# with `invalid byte sequence for encoding "UTF8": 0x00` and the whole capture
# run aborted — six good queries, eight results on the first one, nothing
# stored. A single hostile or malformed byte on one page took out the batch.
#
# Stripped rather than replaced: a NUL carries no meaning in extracted prose, so
# there is nothing to preserve. Other C0 control characters go too (except tab,
# newline, carriage return) because they cannot appear in real body text and
# only ever come from binary served as HTML.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _pg_safe(s: str) -> str:
    """Text that Postgres will actually accept."""
    return _CONTROL.sub("", s or "")


def _strip_html(s: str) -> str:
    """Text only — and an UNCLOSED script/style is stripped to the end.

    🔴 The second substitution is the one that matters, and it was missing.
    `MAX_BYTES` truncates the fetch at 400 KB. Observed 2026-08-08 on
    `mhcautomation.com` (753,559 bytes): the cut landed inside a single huge
    inlined stylesheet, so the captured bytes held **one `<style>` and zero
    `</style>`**. The paired-tag regex below cannot match an opener with no
    closer, so the entire CSS payload survived into `body`, sailed past
    `MIN_BODY_CHARS`, and was stored `substantive = true`.

    A claim then cited it, and the fact-checker was handed 8,000 characters of
    `img:is([sizes=auto i])...` and reported — accurately — "only HTML/CSS
    formatting code with no actual content". The artifact was withheld and the
    reason pointed nowhere near the truncation that caused it.

    Stripping to EOF makes the failure *loud instead of silent*: what is left is
    the title, which is under `MIN_BODY_CHARS`, so `reject_reason` fires and the
    page is never cited. Rejecting a page we could not fully capture is correct;
    citing its stylesheet is not.
    """
    s = re.sub(r"<(script|style)[^>]*>.*?</\1\s*>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<(script|style)[^>]*>.*\Z", " ", s, flags=re.S | re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def _title_of(html: str) -> Optional[str]:
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.S | re.I)
    return _strip_html(m.group(1))[:300] if m else None


# Pages that fetch successfully and contain nothing. Each was observed in the
# first real dossier.
_PLACEHOLDERS = (
    "connecting to the itunes store",   # App Store links do not render server-side
    "just a moment",                    # Cloudflare interstitial
    "enable javascript",
    "access denied",
    "are you a robot",
)
_SERP = re.compile(r"(google\.[a-z.]+/search|bing\.com/search|duckduckgo\.com/\?q=)", re.I)
MIN_BODY_CHARS = 500


@dataclass(frozen=True)
class Captured:
    url: str
    sha256: str
    http_status: int
    live: bool
    title: Optional[str]
    body: str
    mime: Optional[str]
    bytes: int

    @property
    def reject_reason(self) -> Optional[str]:
        """Why this capture cannot support a claim, or None if it can.

        🔴 Fetched-and-hashed is not the same as EVIDENCE. The first real
        dossier counted four "Connecting to the iTunes Store." placeholders and
        three Google result pages toward its total. All seven were genuinely
        retrieved and genuinely hashed, and none of them evidenced anything.
        Counting them is the same species of lie as Pimlico reporting
        `processed=4` when all four prompts had failed.
        """
        if not self.live:
            return "fetch did not return 200"
        low = (self.body or "").lower()
        for p in _PLACEHOLDERS:
            if low.startswith(p) or (len(low) < 2000 and p in low):
                return "placeholder page — no server-rendered content"
        if _SERP.search(self.url or ""):
            return "search engine result page, not source content"
        if len(self.body or "") < MIN_BODY_CHARS:
            return f"too thin to support a claim ({len(self.body or '')} chars)"
        return None

    @property
    def substantive(self) -> bool:
        return self.reject_reason is None


async def fetch(url: str, *, timeout: Optional[int] = None) -> Captured:
    """Fetch a URL and hash exactly what came back.

    🔴 The hash is of the RAW BYTES, before any parsing. Hashing the extracted
    text would mean two different pages with the same visible words collide,
    and that a page that changed its markup looks unchanged. The point of
    content-addressing is that the artifact is what it is.

    A non-200 is captured too, with `live=False`. "We looked and it was gone"
    is evidence; silently dropping it means a dead citation looks the same as
    one that was never attempted.
    """
    t = timeout or DEFAULT_TIMEOUT
    try:
        async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=t,
                                     follow_redirects=True) as c:
            r = await c.get(url)
            raw = r.content[:MAX_BYTES]
            status = r.status_code
            mime = (r.headers.get("content-type") or "").split(";")[0] or None
    except Exception as e:                                       # noqa: BLE001
        log.warning("evidence.fetch_failed", url=url[:120], error=str(e)[:150])
        return Captured(url=url, sha256="", http_status=0, live=False,
                        title=None, body="", mime=None, bytes=0)

    text = raw.decode("utf-8", "replace")
    return Captured(
        url=url,
        sha256=hashlib.sha256(raw).hexdigest(),
        http_status=status,
        live=status == 200 and len(raw) > 0,
        title=_pg_safe(_title_of(text) or "") or None,
        body=_pg_safe(_strip_html(text))[:BODY_CHARS],
        mime=mime,
        bytes=len(raw))


async def record(cap: Captured, *, need_id: int, kind: str = "page",
                 run_id: Optional[int] = None,
                 source_kind: str = "primary") -> Optional[int]:
    """Persist one evidence row. Idempotent on (need_id, sha256).

    Refuses to store a capture with no hash: an evidence row that cannot be
    verified is worse than no row, because a claim can cite it.
    """
    if not cap.sha256:
        log.info("evidence.not_recorded", url=cap.url[:100],
                 reason="no hash — the fetch failed entirely")
        return None

    reason = cap.reject_reason
    if reason:
        log.info("evidence.not_substantive", url=cap.url[:100], reason=reason)

    eid = await db.fetchval(
        """
        INSERT INTO evidence (need_id, url, sha256, fetched_at, http_status, mime,
                              snippet, title, body, bytes, kind, source_kind,
                              live_at_capture, captured_by_step, run_id,
                              substantive, reject_reason)
        VALUES ($1,$2,$3, now(), $4,$5,$6,$7,$8,$9,$10,$11,$12,'research.capture',$13,
                $14,$15)
        -- The unique index is PARTIAL (`WHERE need_id IS NOT NULL`), so the
        -- conflict target must repeat that predicate or postgres cannot match
        -- it and raises "no unique or exclusion constraint matching".
        ON CONFLICT (need_id, sha256) WHERE need_id IS NOT NULL DO NOTHING
        RETURNING id
        """,
        need_id, cap.url[:2000], cap.sha256, cap.http_status, cap.mime,
        cap.body[:1000], cap.title, cap.body, cap.bytes, kind, source_kind,
        cap.live, run_id, cap.substantive, reason)

    if eid is None:                       # already captured — same bytes
        eid = await db.fetchval(
            "SELECT id FROM evidence WHERE need_id = $1 AND sha256 = $2",
            need_id, cap.sha256)
    return int(eid) if eid else None


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

class DuckDuckGoSearch:
    """Search connector. Same contract as every other connector."""
    name = "duckduckgo"
    kind = "api"
    ENDPOINT = "https://lite.duckduckgo.com/lite/"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers={"User-Agent": UA}, timeout=25,
                                 follow_redirects=True)

    async def probe(self) -> ProbeResult:
        try:
            async with self._client() as c:
                r = await c.get(self.ENDPOINT, params={"q": "test"})
            return ProbeResult(ok=r.status_code == 200,
                               detail=f"GET lite.duckduckgo.com -> {r.status_code}")
        except Exception as e:                                   # noqa: BLE001
            return ProbeResult(ok=False, detail=f"{type(e).__name__}: {e}"[:200])

    async def contract_test(self) -> TestResult:
        """Does it still return parseable result links?

        HTML scraping is brittle by nature, which is exactly why it needs a
        contract test rather than trust: the day DuckDuckGo changes its markup,
        this must go dormant rather than quietly return zero competitors.
        """
        try:
            results = await self.search("invoice reconciliation software", limit=5)
        except Exception as e:                                   # noqa: BLE001
            return TestResult(ok=False, detail=f"{type(e).__name__}: {e}"[:200])
        if not results:
            return TestResult(
                ok=False,
                detail="parsed cleanly and found ZERO results — the markup has "
                       "almost certainly changed")
        return TestResult(ok=True, detail=f"{len(results)} results parsed",
                          observed_shape={"first": results[0]["url"][:80]})

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        async with self._client() as c:
            r = await c.post(self.ENDPOINT, data={"q": query})
        if r.status_code != 200:
            raise ConnectorError(f"duckduckgo -> {r.status_code}")

        out: list[dict] = []
        seen: set[str] = set()
        for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', r.text,
                             re.S | re.I):
            href, label = m.group(1), _strip_html(m.group(2))
            if not label or len(label) < 6:
                continue
            # DDG wraps outbound links in a redirector; unwrap to the real URL
            # so the evidence records where the content actually came from.
            if "duckduckgo.com/l/" in href or href.startswith("//duckduckgo.com/l/"):
                q = parse_qs(urlparse("https:" + href if href.startswith("//") else href).query)
                href = unquote((q.get("uddg") or [""])[0])
            if not href.startswith("http"):
                continue
            host = urlparse(href).netloc.lower()
            if not host or "duckduckgo" in host or host in seen:
                continue
            seen.add(host)                       # one result per domain
            out.append({"url": href, "title": label[:300], "domain": host})
            if len(out) >= limit:
                break
        return out


async def capture_search(need_id: int, query: str, *, limit: int = 8,
                         run_id: Optional[int] = None) -> list[int]:
    """Search, then FETCH each result and hash it.

    The search result itself is not evidence — it is a pointer. Recording the
    snippet DuckDuckGo shows would be citing DuckDuckGo's summary of a page
    rather than the page, which is the same paraphrase problem TubeOnAI has
    (DEC-003). We go and get the bytes.
    """
    engine = DuckDuckGoSearch()
    results = await engine.search(query, limit=limit)
    log.info("evidence.search", need_id=need_id, query=query[:80],
             results=len(results))

    ids: list[int] = []
    failed = 0
    for res in results:
        # 🔴 ONE BAD PAGE MUST NOT TAKE OUT THE BATCH.
        #
        # Observed 2026-08-09: a page containing a NUL byte raised
        # `invalid byte sequence for encoding "UTF8": 0x00` out of the INSERT,
        # the exception propagated, and a run with six good queries stored
        # nothing at all — the eight results of the first query included one
        # hostile page and the other five queries never ran.
        #
        # `_pg_safe` fixes that specific byte; this fixes the shape of the
        # failure. Capture walks the open web, so the next malformed thing is a
        # matter of when, and losing a whole run to it is not acceptable.
        try:
            cap = await fetch(res["url"])
            eid = await record(cap, need_id=need_id, kind="page", run_id=run_id)
        except Exception as e:                                   # noqa: BLE001
            failed += 1
            log.warning("evidence.capture_failed", url=res.get("url", "")[:120],
                        error=f"{type(e).__name__}: {str(e)[:150]}")
            continue
        if eid:
            ids.append(eid)
    if failed:
        log.warning("evidence.capture_partial", need_id=need_id,
                    query=query[:80], stored=len(ids), failed=failed)
    return ids


async def capture_signal_urls(need_id: int, *, run_id: Optional[int] = None,
                              limit: int = 12) -> list[int]:
    """Capture the URLs of the signals that produced this need.

    These are the highest-quality evidence available: the actual pages where
    someone described the problem. They cost nothing to find because discovery
    already stored them — Pimlico harvested URLs and then never fetched them.
    """
    rows = await db.fetch(
        """
        SELECT DISTINCT g.url FROM signals g
          JOIN needs n ON n.cluster_id = g.cluster_id
         WHERE n.id = $1 AND g.url IS NOT NULL AND g.url <> ''
         LIMIT $2
        """, need_id, limit)

    ids: list[int] = []
    for r in rows:
        cap = await fetch(r["url"])
        eid = await record(cap, need_id=need_id, kind="signal", run_id=run_id)
        if eid:
            ids.append(eid)
    return ids


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

async def verify_live(need_id: int) -> dict:
    """Re-fetch every evidence URL and check the hash still matches.

    Two distinct failures, and they are not the same thing:
      · `dead`    — the URL no longer resolves. The citation is broken.
      · `changed` — it resolves and the bytes differ. The citation is stale:
                    it may no longer say what we quoted.

    Collapsing them into "unverified" would hide the second, which is the more
    dangerous one — a live link that no longer supports the claim beside it.
    """
    rows = await db.fetch(
        "SELECT id, url, sha256 FROM evidence WHERE need_id = $1 AND url IS NOT NULL",
        need_id)
    out = {"checked": 0, "still_live": 0, "dead": 0, "changed": 0}
    for r in rows:
        out["checked"] += 1
        cap = await fetch(r["url"])
        if not cap.live:
            out["dead"] += 1
            await db.execute(
                "UPDATE evidence SET live_at_capture = FALSE WHERE id = $1", r["id"])
            continue
        out["still_live"] += 1
        if cap.sha256 != r["sha256"]:
            out["changed"] += 1
    if out["dead"] or out["changed"]:
        log.warning("evidence.verification", need_id=need_id, **out)
    return out


async def stats(need_id: int) -> dict:
    row = await db.fetchrow(
        """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE live_at_capture) AS live,
               count(*) FILTER (WHERE live_at_capture AND substantive) AS usable,
               count(*) FILTER (WHERE sha256 <> '') AS hashed,
               count(DISTINCT split_part(replace(replace(url,'https://',''),'http://',''), '/', 1))
                 AS domains
          FROM evidence WHERE need_id = $1
        """, need_id)
    return dict(row) if row else {}
