"""The YouTube Data API v3 connectors (HT-002, 03-PIPELINE §A1b step 1).

⚠️ These are OFFLINE tests against the published API shape. No YouTube key
exists on this host, so unlike the other eight sources this connector was
written from the documented contract rather than from observed responses.
That is a weaker guarantee and it is stated plainly here so nobody reads a
green suite as "verified against the real API" — `contract_test()` against a
real key is what actually closes that gap.

What these tests DO protect is everything that is ours rather than Google's:
the credential never reaching a log line, the refusal to guess a channel, the
2-unit quota path, and playlist tombstones not being counted as signals.
"""
from __future__ import annotations

import httpx
import pytest

from jarvis.connectors import sources
from jarvis.connectors.base import ConnectorError

KEY = "test-key-do-not-log"


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("JPD_YOUTUBE_API_KEY", KEY)


def _mock(handler):
    """Patch _client() so every connector call goes through `handler`."""
    def _factory(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 base_url="https://www.googleapis.com")
    return _factory


def _json(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(status, json=payload)


UPLOADS = {"items": [{"id": "UC_abc",
                      "contentDetails": {"relatedPlaylists": {"uploads": "UU_abc"}}}]}


def _playlist(n: int = 2) -> dict:
    return {"items": [
        {"snippet": {"title": f"How we stopped chasing unpaid invoices {i}",
                     "description": "<p>A long description with markup</p>",
                     "publishedAt": "2026-08-01T10:00:00Z",
                     "channelTitle": "Alex Hormozi",
                     "videoOwnerChannelId": "UC_abc",
                     "videoOwnerChannelTitle": "Alex Hormozi",
                     "resourceId": {"videoId": f"vid{i}"}}}
        for i in range(n)]}


# ---------------------------------------------------------------------------
# the credential
# ---------------------------------------------------------------------------

async def test_the_api_key_never_appears_in_a_detail_string(monkeypatch):
    """🔴 `detail` is persisted to connector_health and printed by `jpd
    connectors`. The key travels as a QUERY PARAMETER, so any message built
    from the URL is a leaked credential in the database and on the terminal.
    """
    def handler(request):
        assert KEY in str(request.url), "precondition: the key really is in the URL"
        return _json(403, {"error": {"errors": [{"reason": "quotaExceeded"}],
                                     "message": "Daily quota exceeded"}})

    monkeypatch.setattr(sources.HttpSource, "_client", _mock(handler))
    probe = await sources.YouTubeDataV3().probe()

    assert probe.ok is False
    assert KEY not in probe.detail
    assert "quotaExceeded" in probe.detail


async def test_absent_key_is_dormant_not_a_crash(monkeypatch):
    """The credential rule: absent means dormant, never a 401 loop."""
    monkeypatch.delenv("JPD_YOUTUBE_API_KEY", raising=False)
    probe = await sources.YouTubeDataV3().probe()
    assert probe.ok is False
    assert "HT-002" in probe.detail


async def test_403_reasons_are_distinguished(monkeypatch):
    """quotaExceeded and keyInvalid are both 403 and need different fixes.

    Reporting only the status code sends the next session to the wrong one.
    """
    for reason in ("quotaExceeded", "keyInvalid", "accessNotConfigured"):
        def handler(request, _r=reason):
            return _json(403, {"error": {"errors": [{"reason": _r}], "message": "no"}})

        monkeypatch.setattr(sources.HttpSource, "_client", _mock(handler))
        result = await sources.YouTubeDataV3().contract_test()
        assert result.ok is False
        assert reason in result.detail


async def test_contract_test_fails_on_a_200_with_the_wrong_shape(monkeypatch):
    """Reachable and authenticated is not parseable. A renamed field yields
    plausible zeros, not errors, and hides for weeks."""
    monkeypatch.setattr(sources.HttpSource, "_client",
                        _mock(lambda r: _json(200, {"kind": "youtube#i18nLanguageListResponse"})))
    result = await sources.YouTubeDataV3().contract_test()
    assert result.ok is False
    assert "shape" in result.detail.lower()


async def test_the_key_connector_is_not_a_source(monkeypatch):
    """It has no `sources` row; harvesting it must raise, not return empty."""
    with pytest.raises(ConnectorError, match="not a source"):
        await sources.YouTubeDataV3().call()


# ---------------------------------------------------------------------------
# refusing to guess
# ---------------------------------------------------------------------------

async def test_refuses_to_resolve_a_channel_it_was_not_told_about(monkeypatch):
    """🔴 A connector that harvests the WRONG channel is worse than a dormant
    one: the wrong channel still yields signals and every gate downstream
    believes them.
    """
    monkeypatch.setattr(sources.YouTubeChannel, "config",
                        _async({"channel": "Alex Hormozi"}))
    monkeypatch.setattr(sources.HttpSource, "_client",
                        _mock(lambda r: pytest.fail("must not call the API")))

    with pytest.raises(ConnectorError, match="Refusing to guess"):
        await sources.YtAlexHormozi().call()


async def test_search_resolution_requires_an_explicit_opt_in(monkeypatch):
    """search.list costs 100 units against a 10,000/day quota."""
    monkeypatch.setattr(sources.YouTubeChannel, "config",
                        _async({"channel": "Alex Hormozi", "allow_search_resolve": True}))
    seen: list[str] = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.endswith("/search"):
            return _json(200, {"items": [{"id": {"channelId": "UC_abc"}}]})
        if request.url.path.endswith("/channels"):
            return _json(200, UPLOADS)
        return _json(200, _playlist())

    monkeypatch.setattr(sources.HttpSource, "_client", _mock(handler))
    monkeypatch.setattr(sources.YouTubeChannel, "_cache", _async(None))

    result = await sources.YtAlexHormozi().call()
    assert result.count == 2
    assert any(p.endswith("/search") for p in seen)


async def test_the_cheap_path_costs_two_units_and_skips_search(monkeypatch):
    """channels.list + playlistItems.list = 2 units. Six channels = 12, not 600."""
    monkeypatch.setattr(sources.YouTubeChannel, "config", _async({"handle": "@AlexHormozi"}))
    seen: list[str] = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.endswith("/channels"):
            assert request.url.params.get("forHandle") == "@AlexHormozi"
            return _json(200, UPLOADS)
        return _json(200, _playlist())

    monkeypatch.setattr(sources.HttpSource, "_client", _mock(handler))
    monkeypatch.setattr(sources.YouTubeChannel, "_cache", _async(None))

    await sources.YtAlexHormozi().call()
    assert not any(p.endswith("/search") for p in seen), "must not spend 100 units"
    assert len(seen) == 2


async def test_a_bare_handle_is_normalised(monkeypatch):
    monkeypatch.setattr(sources.YouTubeChannel, "config", _async({"handle": "AlexHormozi"}))

    def handler(request):
        if request.url.path.endswith("/channels"):
            assert request.url.params.get("forHandle") == "@AlexHormozi"
            return _json(200, UPLOADS)
        return _json(200, _playlist())

    monkeypatch.setattr(sources.HttpSource, "_client", _mock(handler))
    monkeypatch.setattr(sources.YouTubeChannel, "_cache", _async(None))
    assert (await sources.YtAlexHormozi().call()).count == 2


async def test_a_cached_identity_calls_only_the_playlist(monkeypatch):
    """Once resolved, identity is DATA and later harvests cost 1 unit."""
    monkeypatch.setattr(sources.YouTubeChannel, "config",
                        _async({"channel_id": "UC_abc", "uploads_playlist_id": "UU_abc"}))
    seen: list[str] = []

    def handler(request):
        seen.append(request.url.path)
        return _json(200, _playlist())

    monkeypatch.setattr(sources.HttpSource, "_client", _mock(handler))
    await sources.YtAlexHormozi().call()
    assert len(seen) == 1 and seen[0].endswith("/playlistItems")


async def test_an_unknown_handle_raises_rather_than_harvesting_nothing(monkeypatch):
    """An empty `items` for a wrong handle would otherwise read as a quiet
    zero-yield — indistinguishable from a channel that simply did not post."""
    monkeypatch.setattr(sources.YouTubeChannel, "config", _async({"handle": "@typo"}))
    monkeypatch.setattr(sources.HttpSource, "_client",
                        _mock(lambda r: _json(200, {"items": []})))

    with pytest.raises(ConnectorError, match="no channel for handle"):
        await sources.YtAlexHormozi().call()


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def _cheap(monkeypatch, payload: dict):
    monkeypatch.setattr(sources.YouTubeChannel, "config",
                        _async({"channel_id": "UC_abc", "uploads_playlist_id": "UU_abc",
                                "channel": "Alex Hormozi"}))
    monkeypatch.setattr(sources.HttpSource, "_client",
                        _mock(lambda r: _json(200, payload)))


async def test_signals_carry_a_resolvable_url_and_the_creator_as_a_voice(monkeypatch):
    """DEC-004: the author is in the payload already; Pimlico threw it away."""
    _cheap(monkeypatch, _playlist(1))
    result = await sources.YtAlexHormozi().call()

    s = result.signals[0]
    assert s.url == "https://www.youtube.com/watch?v=vid0"
    assert s.source_type == "authority"
    assert s.external_id == "vid0"
    assert s.author is not None
    assert s.author.platform == "youtube"
    assert s.author.display_name == "Alex Hormozi"
    assert "<p>" not in (s.body or ""), "description markup must be stripped"
    assert s.observed_at is not None and s.observed_at.year == 2026


async def test_deleted_and_private_uploads_are_not_signals(monkeypatch):
    """YouTube leaves tombstones in the uploads playlist. Counting them
    inflates yield with nothing, which is the exact failure `HarvestResult`
    exists to make visible."""
    payload = {"items": [
        {"snippet": {"title": "Deleted video", "description": "",
                     "resourceId": {"videoId": "gone"}}},
        {"snippet": {"title": "Private video", "description": "",
                     "resourceId": {"videoId": "hidden"}}},
        _playlist(1)["items"][0],
    ]}
    _cheap(monkeypatch, payload)

    result = await sources.YtAlexHormozi().call()
    assert result.count == 1
    assert result.signals[0].external_id == "vid0"


async def test_a_missing_items_key_raises_instead_of_returning_zero(monkeypatch):
    """A shape change must not present as an empty harvest."""
    _cheap(monkeypatch, {"kind": "youtube#playlistItemListResponse"})
    with pytest.raises(ConnectorError, match="no 'items'"):
        await sources.YtAlexHormozi().call()


async def test_maxresults_is_clamped_to_the_api_limit(monkeypatch):
    """playlistItems.list rejects maxResults > 50 with a 400."""
    seen = {}

    monkeypatch.setattr(sources.YouTubeChannel, "config",
                        _async({"channel_id": "UC_abc", "uploads_playlist_id": "UU_abc"}))

    def handler(request):
        seen["max"] = request.url.params.get("maxResults")
        return _json(200, _playlist())

    monkeypatch.setattr(sources.HttpSource, "_client", _mock(handler))
    await sources.YtAlexHormozi().call(limit=500)
    assert seen["max"] == "50"


# ---------------------------------------------------------------------------
# registry wiring
# ---------------------------------------------------------------------------

async def test_all_six_source_rows_now_have_an_implementation():
    """`jpd connectors orphans` listed these as rows that can never emit."""
    from jarvis.connectors import registry

    for name in ("yt_alex_hormozi", "yt_leila_hormozi", "yt_codie_sanchez",
                 "yt_liam_ottley", "yt_liam_evans", "yt_jack_roberts",
                 "yt_nick_saraev", "yt_my_first_million",
                 # batch 2 — migration 020, operator roster expansion
                 "yt_julian_goldie", "yt_affiliate_marketing_dude",
                 "yt_simplilearn", "yt_chase_h_ai", "yt_starter_story",
                 "yt_shane_hummus", "yt_heygen", "yt_npo_start",
                 "yt_sharran", "yt_brad_sugars", "yt_joanna_wiebe",
                 "yt_mark_kashef", "yt_rob_the_ai_guy", "yt_its_keaton",
                 "yt_solopreneur", "yt_financial_news_oraat",
                 "yt_mark_j_kohler", "yt_life_insurance_academy",
                 "yt_simon_squibb", "yt_diary_of_a_ceo",
                 # migration 021
                 "yt_greg_isenberg", "yt_this_week_in_startups"):
        assert registry.has(name), f"{name} still has no implementation"
        assert registry.get(name).source_type == "authority"


async def test_the_credential_connector_is_registered_as_a_service():
    from jarvis.connectors import registry

    assert registry.has("youtube_data_v3")
    assert registry.get("youtube_data_v3").source_type is None


def _async(value):
    """Build an async method that ignores its args and returns `value`."""
    async def _f(*a, **kw):
        return value
    return _f
