"""Source connectors — the eight that actually work from this host.

Every one of these was **probed from this VPS on 2026-08-07** before it was
written. The dormancy state machine exists to catch a source that dies later;
it is not an excuse to ship connectors that were never going to work.

    hacker_news        200  Algolia API, no key
    github_issues      200  public search API (rate-limited without a token)
    stackoverflow      200  StackExchange API, no key
    sec_edgar          200  ⚠️ ONLY with a compliant User-Agent — see below
    google_suggest     200  autocomplete endpoint
    app_store_reviews  200  iTunes customer-reviews RSS
    product_hunt       200  public RSS

    indie_hackers      200  ⚠️ WAS public RSS. The feed was REMOVED — verified
                            2026-08-08. /feed.xml now serves HTML and the SPA
                            returns a byte-identical shell for every path, so
                            its 200 means nothing. Dormant with a reason; it
                            needs a new transport, not a new URL.

    reddit             403  BLOCKED — www and old.reddit both refuse this
                            datacenter IP. It needs OAuth now. Stays dormant,
                            and the state machine will say so rather than
                            silently returning zero for ever.
    ollama / qdrant    401  reachable via nginx but need API keys.

    yt_* (28)           —   ⚠️ NOT probed from this host: no YouTube Data API
                            v3 key exists here yet (HT-002). They ship DORMANT
                            and cannot reach `live` until a real key passes
                            `youtube_data_v3`'s contract test. Written against
                            the published API contract, not against observed
                            responses — which is a weaker guarantee than the
                            eight above, and the contract test is what closes
                            the gap.

────────────────────────────────────────────────────────────────────────────
🔴 THE SEC USER-AGENT TRAP
────────────────────────────────────────────────────────────────────────────
`sec.gov` returned **503** with an ordinary User-Agent and **200** with
`"Pimlico Services admin@pimlicoservices.com"`. The SEC requires a declared
name + contact address. A 503 reads as "their service is down" and would have
sent a future session chasing an outage that does not exist.

────────────────────────────────────────────────────────────────────────────
Source types, and why the spread matters
────────────────────────────────────────────────────────────────────────────
community (HN, GitHub, StackOverflow) · filing (SEC) · search (Google Suggest)
· review (App Store) · launch (Product Hunt, Indie Hackers)

That is **five of six** types live without a single credential. The cross-source
gate needs ≥2 distinct types, so the funnel can actually promote. Only
`authority` needs a key (HT-002), and by design it can never self-corroborate.
"""
from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote_plus

import httpx
import structlog

from .base import ConnectorError, ProbeResult, TestResult
from .types import Author, HarvestResult, Signal

log = structlog.get_logger("connectors.sources")

UA = "JarvisProductDevelopment/0.1 (research; admin@pimlicoservices.com)"
# The SEC wants a declared name and contact. Anything generic gets 503.
SEC_UA = "Pimlico Services admin@pimlicoservices.com"

DEFAULT_TIMEOUT = 25


def _iso(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        s = str(v).replace("Z", "+00:00")
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:                                            # noqa: BLE001
        return None


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


class HttpSource:
    """Shared behaviour. Subclasses declare endpoints and parse their shape.

    ⚠️ Query terms, seed phrases and app ids live in the `sources.config`
    JSONB column, NOT in these classes. Tuning what a source looks for is the
    single highest-leverage knob on funnel quality, and it must be an UPDATE
    rather than a redeploy — the same rule as gate thresholds and price ratios.
    The class constants below are only fallbacks for a source row with no
    config.
    """
    name: str = ""
    kind: str = "api"
    source_type: str = "community"
    ua: str = UA
    probe_url: str = ""

    async def config(self) -> dict:
        from .. import db
        row = await db.fetchrow("SELECT config FROM sources WHERE name = $1", self.name)
        cfg = row["config"] if row else None
        if isinstance(cfg, str):
            import json as _json
            cfg = _json.loads(cfg)
        return cfg or {}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={"User-Agent": self.ua, "Accept-Encoding": "gzip, deflate"},
            timeout=DEFAULT_TIMEOUT, follow_redirects=True)

    async def probe(self) -> ProbeResult:
        """Cheap: is it reachable and are we allowed in?"""
        try:
            async with self._client() as c:
                r = await c.get(self.probe_url)
            return ProbeResult(ok=r.status_code == 200,
                               detail=f"GET {self.probe_url[:60]} -> {r.status_code}")
        except Exception as e:                                   # noqa: BLE001
            return ProbeResult(ok=False, detail=f"{type(e).__name__}: {e}"[:200])

    async def contract_test(self) -> TestResult:
        """Does the response have the SHAPE we parse?

        Distinct from probe on purpose. A service can be up, authenticated and
        returning 200 having renamed the field we depend on — which yields
        plausible zeros rather than errors, and hides for weeks.
        """
        try:
            result = await self.call(limit=3)
        except ConnectorError as e:
            return TestResult(ok=False, detail=str(e)[:250])
        except Exception as e:                                   # noqa: BLE001
            return TestResult(ok=False, detail=f"{type(e).__name__}: {e}"[:250])

        if result.count == 0:
            return TestResult(
                ok=False,
                detail="parsed cleanly but produced ZERO signals — either the shape "
                       "changed or the source is empty; both need a look")
        s = result.signals[0]
        if not s.concept or not s.external_id:
            return TestResult(ok=False, detail="signal missing concept/external_id")
        return TestResult(ok=True, detail=f"{result.count} signals, shape ok",
                          observed_shape={"first_concept": s.concept[:80],
                                          "has_author": s.author is not None})

    async def call(self, limit: int = 25, **kw: Any) -> HarvestResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# community
# ---------------------------------------------------------------------------

class HackerNews(HttpSource):
    name = "hacker_news"
    source_type = "community"
    probe_url = "https://hn.algolia.com/api/v1/search?query=test&hitsPerPage=1"

    # Pain-shaped queries. Bare topic words return noise; these return people
    # describing a problem, which is the thing the funnel is looking for.
    QUERIES = ("\"we struggle with\"", "\"biggest pain\"", "\"wasting hours\"",
               "\"manual process\"", "\"no good tool\"")

    async def call(self, limit: int = 25, **kw: Any) -> HarvestResult:
        queries = (await self.config()).get("queries") or list(self.QUERIES)
        out: list[Signal] = []
        async with self._client() as c:
            for q in queries:
                url = ("https://hn.algolia.com/api/v1/search_by_date"
                       f"?query={quote_plus(q)}&tags=comment&hitsPerPage={max(1, limit // len(queries))}")
                r = await c.get(url)
                if r.status_code != 200:
                    raise ConnectorError(f"hn -> {r.status_code}")
                body = r.json()
                if "hits" not in body:
                    raise ConnectorError(f"hn response has no 'hits'; keys={list(body)[:6]}")
                for h in body["hits"]:
                    text = _strip_html(h.get("comment_text") or h.get("story_title") or "")
                    if not text:
                        continue
                    author = h.get("author")
                    out.append(Signal(
                        external_id=str(h.get("objectID")),
                        concept=text[:300], body=text, source_type=self.source_type,
                        url=f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                        observed_at=_iso(h.get("created_at")),
                        author=Author(handle=author, platform="hackernews",
                                      profile_url=f"https://news.ycombinator.com/user?id={author}")
                        if author else None,
                        raw={"query": q, "points": h.get("points")}))
        return HarvestResult(self.name, out, f"{len(queries)} queries")


class GitHubIssues(HttpSource):
    name = "github_issues"
    source_type = "community"
    probe_url = "https://api.github.com/rate_limit"

    async def call(self, limit: int = 25, **kw: Any) -> HarvestResult:
        # Unauthenticated search is 10 req/min — one query, and we say so.
        q = kw.get("q") or (await self.config()).get("query") \
            or 'label:"help wanted" state:open in:title workflow' 
        url = (f"https://api.github.com/search/issues?q={quote_plus(q)}"
               f"&per_page={min(limit, 50)}&sort=created&order=desc")
        async with self._client() as c:
            r = await c.get(url, headers={"Accept": "application/vnd.github+json"})
        if r.status_code == 403:
            raise ConnectorError("github rate limit (unauthenticated: 10 req/min)")
        if r.status_code != 200:
            raise ConnectorError(f"github -> {r.status_code}")
        body = r.json()
        if "items" not in body:
            raise ConnectorError(f"github response has no 'items'; keys={list(body)[:6]}")

        out = []
        for i in body["items"]:
            user = (i.get("user") or {}).get("login")
            out.append(Signal(
                external_id=str(i.get("id")),
                concept=(i.get("title") or "")[:300],
                body=_strip_html(i.get("body") or "")[:2000],
                source_type=self.source_type, url=i.get("html_url"),
                observed_at=_iso(i.get("created_at")),
                author=Author(handle=user, platform="github",
                              profile_url=(i.get("user") or {}).get("html_url"))
                if user else None,
                raw={"comments": i.get("comments"), "state": i.get("state")}))
        return HarvestResult(self.name, out, f"q={q[:60]}")


class StackOverflow(HttpSource):
    name = "stackoverflow"
    source_type = "community"
    probe_url = ("https://api.stackexchange.com/2.3/info?site=stackoverflow")

    async def call(self, limit: int = 25, **kw: Any) -> HarvestResult:
        cfg = await self.config()
        tagged = cfg.get("tagged", "automation")
        url = ("https://api.stackexchange.com/2.3/questions"
               f"?site=stackoverflow&pagesize={min(limit, 50)}&order=desc"
               f"&sort=creation&tagged={quote_plus(tagged)}&filter=withbody")
        async with self._client() as c:
            r = await c.get(url)
        if r.status_code != 200:
            raise ConnectorError(f"stackoverflow -> {r.status_code}")
        body = r.json()
        if "items" not in body:
            raise ConnectorError(f"SO response has no 'items'; keys={list(body)[:6]}")

        out = []
        for i in body["items"]:
            owner = i.get("owner") or {}
            out.append(Signal(
                external_id=str(i.get("question_id")),
                concept=(i.get("title") or "")[:300],
                body=_strip_html(i.get("body") or "")[:2000],
                source_type=self.source_type, url=i.get("link"),
                observed_at=_iso(i.get("creation_date")),
                author=Author(handle=str(owner.get("display_name") or owner.get("user_id") or ""),
                              platform="stackoverflow",
                              profile_url=owner.get("link")) if owner.get("display_name") else None,
                raw={"score": i.get("score"), "answers": i.get("answer_count"),
                     "tags": i.get("tags")}))
        return HarvestResult(self.name, out, f"tagged={tagged}")


# ---------------------------------------------------------------------------
# filing
# ---------------------------------------------------------------------------

class SecEdgar(HttpSource):
    name = "sec_edgar"
    source_type = "filing"
    ua = SEC_UA                       # 🔴 a generic UA gets 503, not 403
    probe_url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193&type=10-K&count=1&output=atom"

    async def call(self, limit: int = 25, **kw: Any) -> HarvestResult:
        # Full-text search over recent 10-K risk-factor language. The SIC
        # 6000–6799 exclusion (financial services conflict restriction) is
        # applied downstream at qualification, where the CIK is resolved.
        # 🔴 Date-bounded. Without `dateRange` the search returned filings from
        # 2001, every one of which falls outside the 30-day discovery window —
        # so `filing` contributed exactly nothing while appearing to work.
        from datetime import date, timedelta
        cfg = await self.config()
        q = kw.get("q", cfg.get("query", '"we are unable to" "risk factors"'))
        end = date.today()
        start = end - timedelta(days=int(cfg.get("lookback_days", 90)))
        url = ("https://efts.sec.gov/LATEST/search-index?q="
               f"{quote_plus(q)}&forms=10-K"
               f"&dateRange=custom&startdt={start:%Y-%m-%d}&enddt={end:%Y-%m-%d}")
        async with self._client() as c:
            r = await c.get(url)
        if r.status_code != 200:
            raise ConnectorError(f"sec efts -> {r.status_code}")
        try:
            body = r.json()
        except Exception:                                        # noqa: BLE001
            raise ConnectorError("sec efts returned non-JSON") from None

        hits = ((body.get("hits") or {}).get("hits")) or []
        out = []
        for h in hits[:limit]:
            src = h.get("_source") or {}
            names = src.get("display_names") or []
            company = names[0] if names else None
            adsh = src.get("adsh") or h.get("_id")
            # 🔴 `' '.join(src.get("file_type"))` produced "E X - 9 9 . 1":
            # file_type is a STRING, and join iterates its characters. Every
            # sec_edgar concept was unusable garbage and no gate would ever have
            # matched one. Found by LOOKING at the stored signals, which is only
            # possible because they are stored.
            form = src.get("file_type") or "10-K"
            if isinstance(form, list):
                form = " ".join(str(x) for x in form)
            out.append(Signal(
                external_id=str(adsh),
                concept=f"{company or 'filer'} {form} risk factors: "
                        f"{(src.get('description') or 'material business risk')}"[:280],
                body=json.dumps(src)[:2000],
                source_type=self.source_type,
                url=f"https://www.sec.gov/Archives/edgar/data/{(src.get('ciks') or ['0'])[0]}",
                observed_at=_iso(src.get("file_date")),
                # A filing company is ALWAYS a company voice — never a person.
                author=Author(handle=str((src.get("ciks") or [""])[0]),
                              platform="sec", kind="company",
                              display_name=company, org_name=company)
                if company else None,
                raw={"ciks": src.get("ciks"), "form": src.get("file_type")}))
        return HarvestResult(self.name, out, f"q={q[:50]}")


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

class GoogleSuggest(HttpSource):
    name = "google_suggest"
    source_type = "search"
    probe_url = "https://suggestqueries.google.com/complete/search?client=firefox&q=test"

    SEEDS = ("best software for", "how to automate", "alternative to",
             "why is it so hard to", "tool to stop")

    async def call(self, limit: int = 25, **kw: Any) -> HarvestResult:
        seeds = (await self.config()).get("seeds") or list(self.SEEDS)
        out: list[Signal] = []
        async with self._client() as c:
            for seed in seeds:
                r = await c.get("https://suggestqueries.google.com/complete/search",
                                params={"client": "firefox", "q": seed})
                if r.status_code != 200:
                    raise ConnectorError(f"google suggest -> {r.status_code}")
                try:
                    data = json.loads(r.text)
                except Exception:                                # noqa: BLE001
                    raise ConnectorError("google suggest returned non-JSON") from None
                if not isinstance(data, list) or len(data) < 2:
                    raise ConnectorError(f"unexpected suggest shape: {str(data)[:80]}")
                for phrase in data[1][: max(1, limit // len(seeds))]:
                    out.append(Signal(
                        external_id=f"gs:{phrase}", concept=str(phrase)[:300],
                        source_type=self.source_type,
                        url=f"https://www.google.com/search?q={quote_plus(str(phrase))}",
                        raw={"seed": seed}))
                    # Autocomplete has NO author — deliberately left None
                    # rather than fabricating one.
        return HarvestResult(self.name, out, f"{len(seeds)} seeds")


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------

class AppStoreReviews(HttpSource):
    name = "app_store_reviews"
    source_type = "review"
    probe_url = ("https://itunes.apple.com/us/rss/customerreviews/id=310633997/"
                 "sortBy=mostRecent/json")

    # 🔴 The previous list was WhatsApp (310633997) and Starbucks (331177714),
    # labelled "Slack-ish" and "Numbers" in a comment I never checked. The
    # design says "B2B tooling"; those produced Spanish-language consumer
    # complaints about WhatsApp ads. App ids now live in `sources.config` and
    # every one was verified through the iTunes lookup API.
    APPS = {"618783545": "Slack", "546505307": "Zoom", "489969512": "Asana"}

    async def call(self, limit: int = 25, **kw: Any) -> HarvestResult:
        apps = (await self.config()).get("apps") or list(self.APPS)
        apps = list(apps)[:6]
        # 🔴 `limit` is a TOTAL, not per-app. Multiplying it across 7 apps made
        # this source 188 of 234 signals — 80% of the corpus — which starves
        # cross-source clustering no matter how good the other sources are.
        # A connector that floods the window is as damaging as one that returns
        # nothing; both stop the funnel working.
        per_app = max(1, limit // max(1, len(apps)))
        out: list[Signal] = []
        async with self._client() as c:
            for app_id in apps:
                taken = 0
                r = await c.get(f"https://itunes.apple.com/us/rss/customerreviews/"
                                f"id={app_id}/sortBy=mostRecent/json")
                if r.status_code != 200:
                    raise ConnectorError(f"appstore -> {r.status_code}")
                feed = (r.json() or {}).get("feed") or {}
                entries = feed.get("entry") or []
                if isinstance(entries, dict):
                    entries = [entries]
                for e in entries:
                    if taken >= per_app:
                        break
                    rating = int(((e.get("im:rating") or {}).get("label")) or 0)
                    if rating > 3:                 # 1–3★ only: pain, not praise
                        continue
                    taken += 1
                    title = ((e.get("title") or {}).get("label")) or ""
                    content = ((e.get("content") or {}).get("label")) or ""
                    author = ((e.get("author") or {}).get("name") or {}).get("label")
                    eid = ((e.get("id") or {}).get("label")) or f"{app_id}:{title}"
                    out.append(Signal(
                        external_id=str(eid),
                        concept=f"{title} {content}"[:300].strip(),
                        body=content[:2000], source_type=self.source_type,
                        url=((e.get("link") or {}).get("attributes") or {}).get("href"),
                        # Pseudonymous — evidence only, never contactable.
                        author=Author(handle=author, platform="appstore",
                                      display_name=author) if author else None,
                        raw={"rating": rating, "app_id": app_id}))
        return HarvestResult(self.name, out, f"{len(apps)} apps, 1-3 stars")


# ---------------------------------------------------------------------------
# launch — RSS
# ---------------------------------------------------------------------------

class RssSource(HttpSource):
    feed_url: str = ""
    platform: str = ""

    async def call(self, limit: int = 25, **kw: Any) -> HarvestResult:
        async with self._client() as c:
            r = await c.get(self.feed_url)
        if r.status_code != 200:
            raise ConnectorError(f"{self.name} -> {r.status_code}")
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError as e:
            raise ConnectorError(f"{self.name}: feed is not valid XML: {e}") from None

        items = root.findall(".//item") or root.findall(
            ".//{http://www.w3.org/2005/Atom}entry")
        if not items:
            raise ConnectorError(f"{self.name}: feed has no items/entries")

        out = []
        for it in items[:limit]:
            def txt(tag: str) -> str:
                el = it.find(tag) or it.find("{http://www.w3.org/2005/Atom}" + tag)
                return (el.text or "") if el is not None else ""

            title = txt("title")
            if not title:
                continue
            link_el = it.find("link") or it.find("{http://www.w3.org/2005/Atom}link")
            link = (link_el.text if link_el is not None and link_el.text
                    else (link_el.get("href") if link_el is not None else None))
            creator = txt("{http://purl.org/dc/elements/1.1/}creator") or txt("author")
            out.append(Signal(
                external_id=(txt("guid") or link or title)[:200],
                concept=title[:300],
                body=_strip_html(txt("description") or txt("summary"))[:2000],
                source_type=self.source_type, url=link,
                observed_at=_iso(txt("pubDate")) or _iso(txt("updated")),
                author=Author(handle=creator, platform=self.platform,
                              display_name=creator) if creator else None,
                raw={}))
        return HarvestResult(self.name, out, self.feed_url)


class ProductHunt(RssSource):
    name = "product_hunt"
    kind = "rss"
    source_type = "launch"
    platform = "producthunt"
    feed_url = "https://www.producthunt.com/feed"
    probe_url = "https://www.producthunt.com/feed"


# ---------------------------------------------------------------------------
# authority — YouTube Data API v3 (HT-002)
# ---------------------------------------------------------------------------
#
# 🔴 QUOTA IS THE DESIGN CONSTRAINT. The default key allows 10,000 units/day:
#
#     search.list          100 units   ← identity resolution ONLY, then cached
#     channels.list          1 unit
#     playlistItems.list     1 unit
#
# Harvesting all six tracked channels costs **12 units** on the cheap path and
# **600** on the search path. Resolving by search on every harvest would burn
# the daily quota in sixteen runs and then present as a dead connector — a
# quota 403 and a bad-key 403 are the same status code.
#
# 🔴 THE KEY MUST NEVER REACH A DETAIL STRING. `probe()`/`contract_test()`
# details are persisted to `connector_health` and printed by `jpd connectors`,
# which is why every message below names the *path* and never the URL — the
# API key travels as a query parameter, so a logged URL is a leaked credential.

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"


def _yt_key() -> str:
    return os.environ.get("JPD_YOUTUBE_API_KEY", "")


def _yt_reason(payload: dict) -> str:
    """Google's machine-readable failure reason, which the status code hides.

    `quotaExceeded`, `keyInvalid`, `accessNotConfigured` and `ipRefererBlocked`
    all arrive as **403** and need four different fixes. Reporting only "403"
    sends the next session to the wrong one — the same trap as the three
    guessed Anthropic model names that 404'd and read exactly like a dead key.
    """
    err = payload.get("error") or {}
    errs = err.get("errors") or []
    reason = (errs[0].get("reason") if errs and isinstance(errs[0], dict) else "") or ""
    msg = err.get("message") or ""
    return f"{reason}: {msg}" if reason else msg


class _YouTubeBase(HttpSource):
    """Shared YouTube plumbing: keyed GET that never leaks the key."""

    async def _get(self, c: httpx.AsyncClient, path: str,
                   **params: Any) -> dict:
        key = _yt_key()
        if not key:
            raise ConnectorError("JPD_YOUTUBE_API_KEY absent (HT-002)")
        params["key"] = key
        r = await c.get(f"{YOUTUBE_API}/{path}", params=params)
        try:
            body = r.json()
        except Exception:                                        # noqa: BLE001
            raise ConnectorError(f"{path} -> {r.status_code}, body is not JSON") from None
        if r.status_code != 200:
            # `path`, never `r.url` — see the credential note above.
            raise ConnectorError(f"{path} -> {r.status_code} {_yt_reason(body)}"[:250])
        return body


class YouTubeDataV3(_YouTubeBase):
    """Health of the API KEY itself, separate from any one channel.

    Six `yt_*` sources share one credential. Without this, a dead key makes six
    connectors go dormant with six different channel-shaped error messages and
    nothing says "the key is the problem".

    `i18nLanguages.list` is the probe because it costs 1 unit and depends on no
    channel — so a failure here is unambiguously about the credential.
    """
    name = "youtube_data_v3"
    kind = "api"
    source_type = None                      # a service connector, not a source

    async def probe(self) -> ProbeResult:
        if not _yt_key():
            return ProbeResult(ok=False, detail="JPD_YOUTUBE_API_KEY absent (HT-002)")
        try:
            async with self._client() as c:
                await self._get(c, "i18nLanguages", part="snippet")
            return ProbeResult(ok=True, detail="i18nLanguages -> 200")
        except ConnectorError as e:
            return ProbeResult(ok=False, detail=str(e)[:200])
        except Exception as e:                                   # noqa: BLE001
            return ProbeResult(ok=False, detail=f"{type(e).__name__}: {e}"[:200])

    async def contract_test(self) -> TestResult:
        try:
            async with self._client() as c:
                body = await self._get(c, "i18nLanguages", part="snippet")
        except ConnectorError as e:
            return TestResult(ok=False, detail=str(e)[:250])
        except Exception as e:                                   # noqa: BLE001
            return TestResult(ok=False, detail=f"{type(e).__name__}: {e}"[:250])

        items = body.get("items")
        if not items:
            return TestResult(ok=False,
                              detail="i18nLanguages returned no items — shape changed")
        return TestResult(ok=True, detail=f"key valid, {len(items)} languages",
                          observed_shape={"languages": len(items)})

    async def call(self, limit: int = 25, **kw: Any) -> HarvestResult:
        raise ConnectorError(
            "youtube_data_v3 is a credential-health connector, not a source; "
            "harvest the yt_* sources instead")


class YouTubeChannel(_YouTubeBase):
    """One tracked creator channel — 03-PIPELINE §A1b step 1.

    ⚠️ **Scope.** This emits video-level signals: title, description, publish
    time, and the creator as a `voice`. Steps 2–4 of §A1b (captions → whisper →
    schema'd LLM extraction of a timestamped `evidence_quote`) are NOT here and
    are blocked on the Anthropic budget. Until they exist, an authority signal
    can promote a need; it cannot back a published claim — the same restriction
    DEC-003 puts on TubeOnAI, for the same reason.

    🔴 **This class refuses to guess which channel it is.** Identity comes from
    `sources.config`, in order:

        {"channel_id": "UC..."}   → used directly, 0 units
        {"handle": "@AlexHormozi"} → channels.list?forHandle, 1 unit
        {"channel": "Alex Hormozi", "allow_search_resolve": true}
                                   → search.list, 100 units, cached back

    With none of those it raises rather than searching for a name that looks
    close. A connector that silently harvests the wrong channel is worse than
    one that is dormant, because the wrong channel still yields signals and
    every gate downstream believes them.
    """
    kind = "api"
    source_type = "authority"

    async def _cache(self, **values: str) -> None:
        """Persist resolved ids back into `sources.config`.

        Identity is DATA (the HttpSource contract), so the expensive resolution
        happens once and every later harvest takes the 2-unit path.
        """
        from .. import db
        await db.execute(
            "UPDATE sources SET config = COALESCE(config,'{}'::jsonb) || $2::jsonb "
            "WHERE name = $1", self.name, json.dumps(values))

    async def _identity(self, c: httpx.AsyncClient) -> tuple[str, str]:
        """(channel_id, uploads_playlist_id), resolved as cheaply as possible."""
        cfg = await self.config()
        uploads = cfg.get("uploads_playlist_id")
        channel_id = cfg.get("channel_id")
        if channel_id and uploads:
            return channel_id, uploads

        if not channel_id:
            handle = (cfg.get("handle") or "").strip()
            if handle:
                if not handle.startswith("@"):
                    handle = "@" + handle
                body = await self._get(c, "channels", part="id,contentDetails",
                                       forHandle=handle)
                items = body.get("items") or []
                if not items:
                    raise ConnectorError(
                        f"{self.name}: no channel for handle {handle!r} — check it "
                        f"against the channel's own URL; a wrong handle is a silent "
                        f"empty harvest, not an error")
                channel_id = items[0]["id"]
                uploads = (items[0].get("contentDetails", {})
                           .get("relatedPlaylists", {}).get("uploads"))
                await self._cache(channel_id=channel_id, uploads_playlist_id=uploads)
                return channel_id, uploads

            display = (cfg.get("channel") or "").strip()
            if not (display and cfg.get("allow_search_resolve")):
                raise ConnectorError(
                    f"{self.name}: no channel identity in sources.config. Set "
                    f"{{'handle':'@theirhandle'}} (1 unit) or {{'channel_id':'UC...'}} "
                    f"(0 units), or opt into the 100-unit search with "
                    f"{{'allow_search_resolve':true}}. Refusing to guess.")
            body = await self._get(c, "search", part="snippet", type="channel",
                                   q=display, maxResults=1)
            items = body.get("items") or []
            if not items:
                raise ConnectorError(f"{self.name}: search found no channel for {display!r}")
            channel_id = items[0]["id"]["channelId"]
            await self._cache(channel_id=channel_id)

        body = await self._get(c, "channels", part="contentDetails", id=channel_id)
        items = body.get("items") or []
        if not items:
            raise ConnectorError(f"{self.name}: channel {channel_id!r} returned no items")
        uploads = (items[0].get("contentDetails", {})
                   .get("relatedPlaylists", {}).get("uploads"))
        if not uploads:
            raise ConnectorError(
                f"{self.name}: channel has no uploads playlist in contentDetails — "
                f"the response shape changed")
        await self._cache(uploads_playlist_id=uploads)
        return channel_id, uploads

    async def probe(self) -> ProbeResult:
        """Deliberately probes the KEY, not the channel.

        A probe is meant to answer "are we allowed in". Resolving the channel
        here would spend quota on every health sweep and would report a config
        mistake as an outage.
        """
        return await YouTubeDataV3().probe()

    async def call(self, limit: int = 25, **kw: Any) -> HarvestResult:
        cfg = await self.config()
        async with self._client() as c:
            _, uploads = await self._identity(c)
            body = await self._get(c, "playlistItems", part="snippet",
                                   playlistId=uploads,
                                   maxResults=max(1, min(int(limit), 50)))

        items = body.get("items")
        if items is None:
            raise ConnectorError(
                f"{self.name}: playlistItems response has no 'items'; "
                f"keys={list(body)[:6]}")

        out: list[Signal] = []
        for it in items:
            sn = it.get("snippet") or {}
            title = (sn.get("title") or "").strip()
            vid = ((sn.get("resourceId") or {}).get("videoId") or "").strip()
            # YouTube keeps deleted and private uploads in the playlist with the
            # literal titles below and an empty description. They are not
            # signals; counting them would inflate yield with nothing.
            if not title or not vid or title in ("Deleted video", "Private video"):
                continue
            creator = (sn.get("videoOwnerChannelTitle")
                       or sn.get("channelTitle") or cfg.get("channel") or "").strip()
            owner_id = (sn.get("videoOwnerChannelId") or "").strip()
            desc = _strip_html(sn.get("description") or "")
            out.append(Signal(
                external_id=vid,
                concept=title[:300],
                body=(f"{title}\n\n{desc}" if desc else title)[:2000],
                source_type=self.source_type,
                url=f"https://www.youtube.com/watch?v={vid}",
                observed_at=_iso(sn.get("publishedAt")),
                author=Author(
                    handle=owner_id or creator, platform="youtube", kind="person",
                    display_name=creator or None,
                    profile_url=(f"https://www.youtube.com/channel/{owner_id}"
                                 if owner_id else None)) if (owner_id or creator) else None,
                raw={"playlist_id": uploads, "channel": cfg.get("channel")}))
        return HarvestResult(self.name, out, f"uploads playlist {uploads}")


# One subclass per `sources` row. The channel each one points at is DATA, not
# code — see `_identity`. These carry the name and nothing else on purpose.
class YtAlexHormozi(YouTubeChannel):
    name = "yt_alex_hormozi"


class YtLeilaHormozi(YouTubeChannel):
    name = "yt_leila_hormozi"


class YtCodieSanchez(YouTubeChannel):
    name = "yt_codie_sanchez"


class YtLiamOttley(YouTubeChannel):
    name = "yt_liam_ottley"


class YtLiamEvans(YouTubeChannel):
    name = "yt_liam_evans"


class YtJackRoberts(YouTubeChannel):
    name = "yt_jack_roberts"


class YtNickSaraev(YouTubeChannel):
    name = "yt_nick_saraev"


class YtMyFirstMillion(YouTubeChannel):
    name = "yt_my_first_million"


class YtJulianGoldie(YouTubeChannel):
    name = "yt_julian_goldie"


class YtAffiliateMarketingDude(YouTubeChannel):
    name = "yt_affiliate_marketing_dude"


class YtSimplilearn(YouTubeChannel):
    name = "yt_simplilearn"


class YtChaseHAi(YouTubeChannel):
    name = "yt_chase_h_ai"


class YtStarterStory(YouTubeChannel):
    name = "yt_starter_story"


class YtShaneHummus(YouTubeChannel):
    name = "yt_shane_hummus"


class YtHeygen(YouTubeChannel):
    name = "yt_heygen"


class YtNpoStart(YouTubeChannel):
    name = "yt_npo_start"


class YtSharran(YouTubeChannel):
    name = "yt_sharran"


class YtBradSugars(YouTubeChannel):
    name = "yt_brad_sugars"


class YtJoannaWiebe(YouTubeChannel):
    name = "yt_joanna_wiebe"


class YtMarkKashef(YouTubeChannel):
    name = "yt_mark_kashef"


class YtRobTheAiGuy(YouTubeChannel):
    name = "yt_rob_the_ai_guy"


class YtItsKeaton(YouTubeChannel):
    name = "yt_its_keaton"


class YtSolopreneur(YouTubeChannel):
    name = "yt_solopreneur"


class YtFinancialNewsOraat(YouTubeChannel):
    name = "yt_financial_news_oraat"


class YtMarkJKohler(YouTubeChannel):
    name = "yt_mark_j_kohler"


class YtLifeInsuranceAcademy(YouTubeChannel):
    name = "yt_life_insurance_academy"


class YtSimonSquibb(YouTubeChannel):
    name = "yt_simon_squibb"


class YtDiaryOfACeo(YouTubeChannel):
    name = "yt_diary_of_a_ceo"


# ---------------------------------------------------------------------------
# known-blocked, kept explicit
# ---------------------------------------------------------------------------

class IndieHackers(HttpSource):
    """🔴 The RSS feed is GONE — not moved. Verified from this VPS 2026-08-08.

    `/feed.xml` returns **200 with 322KB of HTML** (the site's activity page),
    which is why the contract test failed on *"feed is not valid XML: not
    well-formed, line 1 column 26"* rather than on a 404. Read as a transient
    markup change, that error sends a session hunting for a fix that will never
    ship. It is a permanent removal.

    Ten candidate paths were probed. **Every one returned 200 with HTML:**
    `/feed.xml` `/feed` `/rss` `/rss.xml` `/atom.xml` `/index.xml`
    `/posts/feed.xml` `/products/feed.xml` and the apex domain. Feedburner 404s.

    🔴 **Those 200s are meaningless** — Indie Hackers is a single-page app that
    serves a shell for every path. Proven the way DEC-003 proved TubeOnAI was
    *not* that trap, by hashing bodies: `/rss` and
    `/this-path-is-nonsense-9f3a2b` are **byte-identical** (sha `f1d0a999…`,
    22,115 bytes both). A status code is not evidence when every path returns
    one.

    Also checked and absent: any `<link rel="alternate">` autodiscovery tag on
    the homepage, and any JSON transport — `/api/posts`, `/api/v1/posts`,
    `/api/feed`, `/graphql`, `/_next/data` all return that same 404 shell.

    Kept as a real class rather than deleted, for the reason `Reddit` is: a
    connector that is **missing** and one that is **dead** need different fixes,
    and deleting it would silently drop a seeded `sources` row into
    `rows_without_code`. The `launch` source type is not lost — `product_hunt`
    runs on the same `RssSource` base and still returns valid Atom, which is
    also what rules out a defect in our parser.

    ⚠️ `probe_url` is deliberately kept. The probe PASSES (the host is up and
    serving) while the contract test FAILS — the textbook "reachable is not
    parseable" split from §3, and the reason a probe alone must never grant
    `live`.
    """
    name = "indie_hackers"
    kind = "rss"
    source_type = "launch"
    platform = "indiehackers"
    probe_url = "https://www.indiehackers.com/feed.xml"

    async def call(self, limit: int = 25, **kw: Any) -> HarvestResult:
        raise ConnectorError(
            "indiehackers.com no longer publishes a feed: /feed.xml and nine "
            "other candidate paths all return 200 with HTML, and the SPA serves "
            "a byte-identical shell for nonsense paths. No rel=alternate tag "
            "and no JSON API. Verified 2026-08-08 — this needs a new transport, "
            "not a new URL.")


class Reddit(HttpSource):
    """403 from this VPS on both www.reddit.com and old.reddit.com — verified
    2026-08-07. Reddit requires OAuth from datacenter IPs now.

    With script-app credentials (JPD_REDDIT_CLIENT_ID / JPD_REDDIT_CLIENT_SECRET,
    from https://www.reddit.com/prefs/apps) it authenticates via the
    client_credentials grant and reads through oauth.reddit.com. Without them it
    stays **dormant with a reason** rather than the source simply not existing —
    a missing connector and a blocked one need different fixes.
    """
    name = "reddit"
    source_type = "community"
    probe_url = "https://www.reddit.com/r/smallbusiness/new.json?limit=1"

    SUBREDDITS = ("smallbusiness", "Entrepreneur", "sweatystartup")

    @staticmethod
    def _creds() -> tuple[str, str]:
        return (os.environ.get("JPD_REDDIT_CLIENT_ID", ""),
                os.environ.get("JPD_REDDIT_CLIENT_SECRET", ""))

    async def _token(self, c: httpx.AsyncClient) -> str:
        cid, secret = self._creds()
        r = await c.post("https://www.reddit.com/api/v1/access_token",
                         auth=(cid, secret), data={"grant_type": "client_credentials"})
        if r.status_code != 200:
            raise ConnectorError(f"reddit oauth token -> {r.status_code}")
        tok = r.json().get("access_token")
        if not tok:
            raise ConnectorError("reddit oauth response carried no access_token")
        return tok

    async def probe(self) -> ProbeResult:
        cid, secret = self._creds()
        if not (cid and secret):
            return ProbeResult(
                ok=False,
                detail="reddit returns 403 to this datacenter IP unauthenticated; "
                       "JPD_REDDIT_CLIENT_ID/SECRET absent (script app at "
                       "reddit.com/prefs/apps)")
        try:
            async with self._client() as c:
                await self._token(c)
            return ProbeResult(ok=True, detail="oauth token grant -> 200")
        except ConnectorError as e:
            return ProbeResult(ok=False, detail=str(e)[:200])
        except Exception as e:                                   # noqa: BLE001
            return ProbeResult(ok=False, detail=f"{type(e).__name__}: {e}"[:200])

    async def call(self, limit: int = 25, **kw: Any) -> HarvestResult:
        cid, secret = self._creds()
        if not (cid and secret):
            raise ConnectorError(
                "reddit returns 403 to this datacenter IP on both www and old hosts; "
                "it needs OAuth credentials (JPD_REDDIT_CLIENT_ID/SECRET). "
                "Verified 2026-08-07.")
        subs = (await self.config()).get("subreddits") or list(self.SUBREDDITS)
        per = max(1, limit // len(subs))
        out: list[Signal] = []
        async with self._client() as c:
            tok = await self._token(c)
            for sub in subs:
                r = await c.get(
                    f"https://oauth.reddit.com/r/{sub}/new",
                    params={"limit": per},
                    headers={"Authorization": f"bearer {tok}"})
                if r.status_code != 200:
                    raise ConnectorError(f"reddit /r/{sub}/new -> {r.status_code}")
                body = r.json()
                children = (body.get("data") or {}).get("children")
                if children is None:
                    raise ConnectorError(
                        f"reddit listing has no data.children; keys={list(body)[:6]}")
                for ch in children:
                    d = ch.get("data") or {}
                    text = _strip_html(d.get("selftext") or d.get("title") or "")
                    if not text:
                        continue
                    author = d.get("author")
                    out.append(Signal(
                        external_id=str(d.get("name") or d.get("id")),
                        concept=(d.get("title") or text)[:300], body=text,
                        source_type=self.source_type,
                        url=f"https://www.reddit.com{d.get('permalink', '')}",
                        observed_at=_iso(d.get("created_utc")),
                        author=Author(handle=author, platform="reddit",
                                      profile_url=f"https://www.reddit.com/user/{author}")
                        if author and author != "[deleted]" else None,
                        raw={"subreddit": sub, "ups": d.get("ups")}))
        return HarvestResult(self.name, out, f"{len(subs)} subreddits via oauth")


ALL: tuple[type[HttpSource], ...] = (
    HackerNews, GitHubIssues, StackOverflow, SecEdgar, GoogleSuggest,
    AppStoreReviews, ProductHunt, IndieHackers, Reddit,
    YtAlexHormozi, YtLeilaHormozi, YtCodieSanchez,
    YtLiamOttley, YtLiamEvans, YtJackRoberts,
    YtNickSaraev, YtMyFirstMillion,
    YtJulianGoldie, YtAffiliateMarketingDude, YtSimplilearn, YtChaseHAi,
    YtStarterStory, YtShaneHummus, YtHeygen, YtNpoStart,
    YtSharran, YtBradSugars, YtJoannaWiebe, YtMarkKashef,
    YtRobTheAiGuy, YtItsKeaton, YtSolopreneur, YtFinancialNewsOraat,
    YtMarkJKohler, YtLifeInsuranceAcademy, YtSimonSquibb, YtDiaryOfACeo,
)
