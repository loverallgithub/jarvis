"""Two LLM routes: `api.anthropic.com`, then OpenRouter.

Added 2026-08-08. The Anthropic key hit a self-imposed spend cap mid-forge and
— correctly — left three finished artifacts unofferable, because the factual
pass could not run and *"an unverifiable claim is not a verified claim"*. One
provider was a single point of failure on the only step that costs real money.

🔴 What these tests are really guarding is that the fallback changed **who
serves the call** and nothing else. A second route is a liability if it also
becomes a second way to call something verified that was not: `None` from both
routes must still be `None`, and an error must never come back shaped like
content (the Sintra failure — `f"[Automation failed: {e}]"` was a perfectly
ordinary string, indistinguishable from real output, and it reached a live
LinkedIn account six days running).
"""
from __future__ import annotations

import httpx
import pytest

from jarvis.forge import build

ANTH = "https://api.anthropic.com/v1/messages"
OR = "https://openrouter.ai/api/v1/chat/completions"

CAPPED = {"type": "error", "error": {
    "type": "invalid_request_error",
    "message": "You have reached your specified API usage limits. "
               "You will regain access on 2026-09-01 at 00:00 UTC."}}


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("JPD_ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("JPD_OPENROUTER_KEY", "sk-or-test")
    monkeypatch.setattr(build.ev, "params", _async({"forge_model": "claude-opus-5"}))


def _routes(monkeypatch, handler):
    """Route every httpx call in build.py through `handler`."""
    real = httpx.AsyncClient

    def factory(*a, **kw):
        return real(transport=httpx.MockTransport(handler), timeout=kw.get("timeout", 30))

    monkeypatch.setattr(build.httpx, "AsyncClient", factory)


def anthropic_ok(text="from anthropic"):
    return httpx.Response(200, json={"content": [
        {"type": "thinking", "thinking": "..."},      # the block that broke it once
        {"type": "text", "text": text}]})


def openrouter_ok(text="from openrouter"):
    return httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"total_tokens": 42}})


# ---------------------------------------------------------------------------
# routing
# ---------------------------------------------------------------------------

async def test_anthropic_is_tried_first_and_openrouter_is_not_called(monkeypatch):
    """The working path must be untouched — and must not double-spend."""
    seen = []

    def h(request):
        seen.append(str(request.url))
        return anthropic_ok() if ANTH in str(request.url) else pytest.fail("must not")

    _routes(monkeypatch, h)
    assert await build._llm("x") == "from anthropic"
    assert len(seen) == 1 and ANTH in seen[0]


async def test_the_spend_cap_falls_through_to_openrouter(monkeypatch):
    """The exact 400 that stopped the forge on 2026-08-07."""
    seen = []

    def h(request):
        u = str(request.url); seen.append(u)
        return httpx.Response(400, json=CAPPED) if ANTH in u else openrouter_ok()

    _routes(monkeypatch, h)
    assert await build._llm("x") == "from openrouter"
    assert any(OR in u for u in seen), "fallback route was never called"


async def test_a_200_with_no_text_block_also_falls_through(monkeypatch):
    """Lesson 36's failure is a failure, not an empty success — so it must
    fall through rather than silently return None with a route still available."""
    def h(request):
        if ANTH in str(request.url):
            return httpx.Response(200, json={"content": [{"type": "thinking",
                                                          "thinking": "..."}]})
        return openrouter_ok()

    _routes(monkeypatch, h)
    assert await build._llm("x") == "from openrouter"


async def test_a_network_error_falls_through(monkeypatch):
    def h(request):
        if ANTH in str(request.url):
            raise httpx.ConnectError("boom")
        return openrouter_ok()

    _routes(monkeypatch, h)
    assert await build._llm("x") == "from openrouter"


async def test_openrouter_alone_works_when_anthropic_is_unset(monkeypatch):
    monkeypatch.delenv("JPD_ANTHROPIC_API_KEY", raising=False)

    def h(request):
        assert ANTH not in str(request.url), "must not call an unkeyed provider"
        return openrouter_ok()

    _routes(monkeypatch, h)
    assert await build._llm("x") == "from openrouter"


# ---------------------------------------------------------------------------
# the model id is never guessed
# ---------------------------------------------------------------------------

MODELS = {"data": [{"id": "anthropic/claude-opus-5"},
                   {"id": "anthropic/claude-haiku-4.5"},
                   {"id": "anthropic/claude-sonnet-5"}]}
OR_MODELS = "https://openrouter.ai/api/v1/models"


@pytest.fixture(autouse=True)
def _clear_model_cache():
    build._OR_MODELS = set()
    yield
    build._OR_MODELS = set()


async def test_an_anthropic_dated_id_resolves_to_openrouters_id(monkeypatch):
    """🔴 The two providers DO NOT share an id space, and assuming they did
    cost a whole forge run: `verify_model` is `claude-haiku-4-5-20251001`, which
    became `anthropic/claude-haiku-4-5-20251001` and returned 400 "not a valid
    model ID" **42 times**. Generation succeeded, every factual check failed,
    nothing became offerable. Resolve against the served list; never assume."""
    monkeypatch.setattr(build.ev, "params",
                        _async({"verify_model": "claude-haiku-4-5-20251001"}))
    sent = {}

    def h(request):
        u = str(request.url)
        if OR_MODELS in u:
            return httpx.Response(200, json=MODELS)
        if ANTH in u:
            return httpx.Response(400, json=CAPPED)
        import json as _j
        sent["model"] = _j.loads(request.content)["model"]
        return openrouter_ok()

    _routes(monkeypatch, h)
    assert await build._llm("x", model_param="verify_model") == "from openrouter"
    assert sent["model"] == "anthropic/claude-haiku-4.5"


async def test_a_model_the_route_does_not_serve_fails_loudly(monkeypatch):
    """Better an honest None than a guessed id that 400s per claim."""
    monkeypatch.setattr(build.ev, "params", _async({"forge_model": "claude-imaginary-9"}))

    def h(request):
        u = str(request.url)
        if OR_MODELS in u:
            return httpx.Response(200, json=MODELS)
        if ANTH in u:
            return httpx.Response(400, json=CAPPED)
        pytest.fail("must not POST an unresolvable model")

    _routes(monkeypatch, h)
    assert await build._llm("x") is None


async def test_the_bare_model_id_is_namespaced_for_openrouter(monkeypatch):
    """`research_params` holds `claude-opus-5`; OpenRouter needs
    `anthropic/claude-opus-5`. Prefixed in code so the id is not duplicated in
    the database and cannot drift between the two routes."""
    sent = {}

    def h(request):
        u = str(request.url)
        if OR_MODELS in u:
            return httpx.Response(200, json=MODELS)
        if ANTH in u:
            return httpx.Response(400, json=CAPPED)
        import json as _j
        sent["model"] = _j.loads(request.content)["model"]
        return openrouter_ok()

    _routes(monkeypatch, h)
    await build._llm("x")
    assert sent["model"] == "anthropic/claude-opus-5"


async def test_an_already_namespaced_id_is_not_double_prefixed(monkeypatch):
    monkeypatch.setattr(build.ev, "params",
                        _async({"forge_model": "anthropic/claude-opus-5"}))
    sent = {}

    def h(request):
        if ANTH in str(request.url):
            return httpx.Response(400, json=CAPPED)
        import json as _j
        sent["model"] = _j.loads(request.content)["model"]
        return openrouter_ok()

    _routes(monkeypatch, h)
    await build._llm("x")
    assert sent["model"] == "anthropic/claude-opus-5"


# ---------------------------------------------------------------------------
# 🔴 a second route must not become a second way to fabricate
# ---------------------------------------------------------------------------

async def test_both_routes_failing_returns_None_not_a_string(monkeypatch):
    """The Sintra shape. An error described in the type used for success is
    indistinguishable from success at every layer downstream."""
    _routes(monkeypatch, lambda r: httpx.Response(500, json={"error": "nope"}))
    out = await build._llm("x")
    assert out is None
    assert not isinstance(out, str)


async def test_an_openrouter_404_returns_None(monkeypatch):
    """A wrong model id 404s with 'No endpoints found', which reads exactly
    like a dead key. It is still a failure and must produce None."""
    def h(request):
        if ANTH in str(request.url):
            return httpx.Response(400, json=CAPPED)
        return httpx.Response(404, json={"error": {
            "message": "No endpoints found for anthropic/claude-3.5-haiku.",
            "code": 404}})

    _routes(monkeypatch, h)
    assert await build._llm("x") is None


async def test_an_empty_completion_is_a_failure_not_empty_content(monkeypatch):
    def h(request):
        if ANTH in str(request.url):
            return httpx.Response(400, json=CAPPED)
        return httpx.Response(200, json={"choices": [{"message": {"content": "   "}}]})

    _routes(monkeypatch, h)
    assert await build._llm("x") is None


async def test_a_truncated_completion_is_reported_as_a_budget_problem(monkeypatch):
    """🔴 An extended-thinking model spends `max_tokens` on reasoning FIRST, so
    too small a budget returns 200 with `finish_reason="length"` and empty
    content — a BUDGET problem that reads exactly like a broken provider.
    Observed live 2026-08-08: opus-5 at max_tokens=16 returns nothing, at 64
    returns the answer. The log must name the cause."""
    def h(request):
        if ANTH in str(request.url):
            return httpx.Response(400, json=CAPPED)
        return httpx.Response(200, json={"choices": [
            {"finish_reason": "length", "message": {"content": ""}}]})

    import structlog

    _routes(monkeypatch, h)
    # structlog does not propagate to pytest's caplog here; capture its own.
    with structlog.testing.capture_logs() as logs:
        assert await build._llm("x") is None
    trunc = [e for e in logs if e.get("event") == "forge.llm_fallback_no_text"]
    assert trunc, "the truncation must be logged, not swallowed"
    assert trunc[0]["finish_reason"] == "length"
    assert "max_tokens too small" in trunc[0]["hint"]


async def test_no_keys_at_all_returns_None(monkeypatch):
    monkeypatch.delenv("JPD_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("JPD_OPENROUTER_KEY", raising=False)
    _routes(monkeypatch, lambda r: pytest.fail("must not call anything"))
    assert await build._llm("x") is None


def test_the_two_parsers_are_separate_and_reject_each_others_payloads():
    """Handing one parser the other's shape must yield None, not a crash and
    not a lucky partial read."""
    anth = {"content": [{"type": "text", "text": "a"}]}
    oai = {"choices": [{"message": {"content": "b"}}]}
    assert build._text_of(anth) == "a"
    assert build._text_of_openai(oai) == "b"
    assert build._text_of(oai) is None
    assert build._text_of_openai(anth) is None


def _async(value):
    async def _f(*a, **kw):
        return value
    return _f
