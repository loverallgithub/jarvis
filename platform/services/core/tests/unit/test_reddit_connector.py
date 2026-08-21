"""Offline tests for the Reddit OAuth source connector."""
from __future__ import annotations

import httpx
import pytest

from jarvis.connectors import sources
from jarvis.connectors.base import ConnectorError


def _mock(handler):
    def factory(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return factory


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    monkeypatch.setenv("JPD_REDDIT_CLIENT_ID", "client-id")
    monkeypatch.setenv("JPD_REDDIT_CLIENT_SECRET", "client-secret")


async def test_placeholder_credentials_keep_the_connector_dormant(monkeypatch):
    monkeypatch.setenv("JPD_REDDIT_CLIENT_ID", "CHANGE_ME")
    monkeypatch.setattr(sources.HttpSource, "_client",
                        _mock(lambda request: pytest.fail("must not call Reddit")))

    result = await sources.Reddit().probe()

    assert result.ok is False
    assert "absent" in result.detail


async def test_non_json_token_response_is_a_connector_error(monkeypatch):
    monkeypatch.setattr(
        sources.HttpSource, "_client",
        _mock(lambda request: httpx.Response(200, text="not-json")))

    with pytest.raises(ConnectorError, match="not JSON"):
        async with sources.Reddit()._client() as client:
            await sources.Reddit()._token(client)


async def test_harvest_never_exceeds_the_requested_limit(monkeypatch):
    monkeypatch.setattr(sources.Reddit, "config", lambda self: _async({
        "subreddits": ["one", "two", "three"],
    }))

    def handler(request):
        if request.url.path == "/api/v1/access_token":
            return httpx.Response(200, json={"access_token": "token"})
        sub = request.url.path.split("/")[2]
        return httpx.Response(200, json={"data": {"children": [{"data": {
            "name": f"t3_{sub}",
            "title": f"A sufficiently descriptive problem from {sub}",
            "selftext": "This manual process wastes several hours every week.",
            "permalink": f"/r/{sub}/comments/1/test/",
            "created_utc": 1_700_000_000,
            "author": "operator",
        }}]}})

    monkeypatch.setattr(sources.HttpSource, "_client", _mock(handler))

    result = await sources.Reddit().call(limit=1)

    assert result.count == 1


async def _async(value):
    return value
