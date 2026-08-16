"""Service connectors — ollama, qdrant, anthropic, duckduckgo.

Credentials for the first three were copied from the Pimlico stack on
2026-08-07: same keys, same accounts, deliberately not new ones. A second key
to rotate is a second key to forget.

They go through the identical `probe` / `contract_test` / dormancy machinery as
every source connector. Having a working credential is not the same as being
`live` — the shape still has to be verified, and a credential that stops working
must walk the connector to dormant rather than producing plausible zeros.
"""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx
import structlog

from .base import ConnectorError, ProbeResult, TestResult

log = structlog.get_logger("connectors.services")


def _configured(v: str) -> bool:
    return bool(v) and v != "CHANGE_ME"


class OllamaConnector:
    """Local embedding host, reached through its nginx vhost.

    ⚠️ Authenticated with an `x-api-key` header, NOT a bearer token — the key
    is set literally in the vhost (`set $api_key`). Reaching it directly at
    172.17.0.1:32768 does NOT work from an overlay container.
    """
    name = "ollama"
    kind = "api"

    @property
    def url(self) -> str:
        return os.environ.get("JPD_OLLAMA_URL", "").rstrip("/")

    @property
    def key(self) -> str:
        return os.environ.get("JPD_OLLAMA_API_KEY", "")

    @property
    def model(self) -> str:
        return os.environ.get("JPD_EMBED_MODEL", "nomic-embed-text")

    async def probe(self) -> ProbeResult:
        if not (_configured(self.key) and self.url):
            return ProbeResult(ok=False, detail="JPD_OLLAMA_URL/API_KEY absent")
        try:
            async with httpx.AsyncClient(timeout=25) as c:
                r = await c.get(f"{self.url}/api/tags", headers={"x-api-key": self.key})
            return ProbeResult(ok=r.status_code == 200, detail=f"/api/tags -> {r.status_code}")
        except Exception as e:                                   # noqa: BLE001
            return ProbeResult(ok=False, detail=f"{type(e).__name__}: {e}"[:200])

    async def contract_test(self) -> TestResult:
        """Reachable is not enough — the embedding model must exist AND return
        a vector of the width we store."""
        if not (_configured(self.key) and self.url):
            return TestResult(ok=False, detail="credentials absent")
        try:
            async with httpx.AsyncClient(timeout=90) as c:
                tags = await c.get(f"{self.url}/api/tags", headers={"x-api-key": self.key})
                if tags.status_code != 200:
                    return TestResult(ok=False, detail=f"/api/tags -> {tags.status_code}")
                names = [m.get("name", "") for m in (tags.json().get("models") or [])]
                if not any(self.model in n for n in names):
                    return TestResult(
                        ok=False,
                        detail=f"model {self.model!r} not present; have: {names[:6]}")

                emb = await c.post(f"{self.url}/api/embeddings",
                                   headers={"x-api-key": self.key},
                                   json={"model": self.model, "prompt": "contract test"})
            if emb.status_code != 200:
                return TestResult(ok=False, detail=f"/api/embeddings -> {emb.status_code}")
            vec = emb.json().get("embedding") or []
            if len(vec) < 64:
                return TestResult(ok=False,
                                  detail=f"embedding has {len(vec)} dims — implausible")
            return TestResult(ok=True, detail=f"{self.model}: {len(vec)} dims",
                              observed_shape={"dims": len(vec), "models": len(names)})
        except Exception as e:                                   # noqa: BLE001
            return TestResult(ok=False, detail=f"{type(e).__name__}: {e}"[:200])

    async def embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=90) as c:
            r = await c.post(f"{self.url}/api/embeddings",
                             headers={"x-api-key": self.key},
                             json={"model": self.model, "prompt": text[:8000]})
        if r.status_code != 200:
            raise ConnectorError(f"ollama embeddings -> {r.status_code}")
        return list(r.json().get("embedding") or [])


class QdrantConnector:
    """Vector store. Authenticated with an `api-key` header."""
    name = "qdrant"
    kind = "api"

    @property
    def url(self) -> str:
        return os.environ.get("JPD_QDRANT_URL", "").rstrip("/")

    @property
    def key(self) -> str:
        return os.environ.get("JPD_QDRANT_API_KEY", "")

    def _h(self) -> dict[str, str]:
        return {"api-key": self.key, "content-type": "application/json"}

    async def probe(self) -> ProbeResult:
        if not (_configured(self.key) and self.url):
            return ProbeResult(ok=False, detail="JPD_QDRANT_URL/API_KEY absent")
        try:
            async with httpx.AsyncClient(timeout=25) as c:
                r = await c.get(f"{self.url}/collections", headers=self._h())
            return ProbeResult(ok=r.status_code == 200, detail=f"/collections -> {r.status_code}")
        except Exception as e:                                   # noqa: BLE001
            return ProbeResult(ok=False, detail=f"{type(e).__name__}: {e}"[:200])

    async def contract_test(self) -> TestResult:
        if not (_configured(self.key) and self.url):
            return TestResult(ok=False, detail="credentials absent")
        try:
            async with httpx.AsyncClient(timeout=25) as c:
                r = await c.get(f"{self.url}/collections", headers=self._h())
            if r.status_code != 200:
                return TestResult(ok=False, detail=f"/collections -> {r.status_code}")
            body = r.json()
            if "result" not in body or "collections" not in body["result"]:
                return TestResult(ok=False,
                                  detail=f"unexpected shape; keys={list(body)[:6]}")
            names = [c["name"] for c in body["result"]["collections"]]
            # ⚠️ `pimlico_signals` belongs to the OTHER platform. JPD must
            # create and use its own collection and never write to that one.
            return TestResult(ok=True, detail=f"{len(names)} collections",
                              observed_shape={"collections": names[:8]})
        except Exception as e:                                   # noqa: BLE001
            return TestResult(ok=False, detail=f"{type(e).__name__}: {e}"[:200])


class AnthropicConnector:
    """LLM for extraction and synthesis.

    🔴 Model names must come from `/v1/models`, not from memory. Guessing
    `claude-3-5-haiku-20241022` and two other plausible names returned 404
    `not_found_error` for all three, which looks exactly like a bad key. The
    key was fine; this account serves claude-opus-5 / sonnet-5 / haiku-4-5.
    """
    name = "anthropic"
    kind = "api"

    @property
    def key(self) -> str:
        return os.environ.get("JPD_ANTHROPIC_API_KEY", "")

    def _h(self) -> dict[str, str]:
        return {"x-api-key": self.key, "anthropic-version": "2023-06-01",
                "content-type": "application/json"}

    async def probe(self) -> ProbeResult:
        if not _configured(self.key):
            return ProbeResult(ok=False, detail="JPD_ANTHROPIC_API_KEY absent")
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get("https://api.anthropic.com/v1/models", headers=self._h())
            return ProbeResult(ok=r.status_code == 200, detail=f"/v1/models -> {r.status_code}")
        except Exception as e:                                   # noqa: BLE001
            return ProbeResult(ok=False, detail=f"{type(e).__name__}: {e}"[:200])

    async def contract_test(self) -> TestResult:
        if not _configured(self.key):
            return TestResult(ok=False, detail="credentials absent")
        try:
            async with httpx.AsyncClient(timeout=60) as c:
                m = await c.get("https://api.anthropic.com/v1/models", headers=self._h())
                if m.status_code != 200:
                    return TestResult(ok=False, detail=f"/v1/models -> {m.status_code}")
                ids = [x["id"] for x in m.json().get("data", [])]
                want = os.environ.get("JPD_LLM_MODEL", "claude-haiku-4-5-20251001")
                if want not in ids:
                    return TestResult(
                        ok=False,
                        detail=f"configured model {want!r} is not served by this key; "
                               f"available: {ids[:5]}")
                r = await c.post("https://api.anthropic.com/v1/messages",
                                 headers=self._h(),
                                 json={"model": want, "max_tokens": 12,
                                       "messages": [{"role": "user",
                                                     "content": "Reply with: ok"}]})
            if r.status_code != 200:
                return TestResult(ok=False, detail=f"messages -> {r.status_code}")
            return TestResult(ok=True, detail=f"{want} responded",
                              observed_shape={"models": len(ids)})
        except Exception as e:                                   # noqa: BLE001
            return TestResult(ok=False, detail=f"{type(e).__name__}: {e}"[:200])


class OpenRouterConnector:
    """The SECOND LLM route, so a capped provider cannot stop the forge.

    Registered as a real connector rather than assumed, because
    `forge.generate` is gated on connector HEALTH, not on a key being present.
    A fallback the health system cannot see is a fallback the step engine will
    refuse to use — which is exactly what happened on 2026-08-08: `_llm` could
    reach OpenRouter, and the step still returned `skipped_dormant` because
    `anthropic` was the only route it knew about.

    🔴 Same rule as Anthropic: **model ids come from `/api/v1/models`, never
    from memory.** Guessing `anthropic/claude-3.5-haiku` returned
    `404 "No endpoints found"`, which reads exactly like a dead key.
    """
    name = "openrouter"
    kind = "api"

    @property
    def key(self) -> str:
        return os.environ.get("JPD_OPENROUTER_KEY", "")

    def _h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key}", "content-type": "application/json"}

    @staticmethod
    async def _routed(c: httpx.AsyncClient, model: str) -> Optional[str]:
        """Resolve via the SAME function `_llm` uses.

        🔴 A contract test that resolves the model differently from the code
        path is worse than no contract test: it would pass on
        `anthropic/claude-opus-5` while the verifier died forty-two times on
        `anthropic/claude-haiku-4-5-20251001`. Which is exactly what happened.
        """
        from ..forge.build import _resolve_openrouter_model
        return await _resolve_openrouter_model(c, model)

    async def probe(self) -> ProbeResult:
        """`/api/v1/key` — cheap, and it also proves the credit limit is intact.

        A key with a spent daily limit is exactly the condition that made the
        primary route useless, so the fallback must be able to report it.
        """
        if not _configured(self.key):
            return ProbeResult(ok=False, detail="JPD_OPENROUTER_KEY absent")
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get("https://openrouter.ai/api/v1/key", headers=self._h())
            if r.status_code != 200:
                return ProbeResult(ok=False, detail=f"/api/v1/key -> {r.status_code}")
            d = (r.json() or {}).get("data") or {}
            rem, lim = d.get("limit_remaining"), d.get("limit")
            if rem is not None and rem <= 0:
                return ProbeResult(ok=False,
                                   detail=f"credit exhausted: {rem} of {lim} remaining")
            return ProbeResult(ok=True, detail=f"key ok, {rem} of {lim} remaining")
        except Exception as e:                                   # noqa: BLE001
            return ProbeResult(ok=False, detail=f"{type(e).__name__}: {e}"[:200])

    async def contract_test(self) -> TestResult:
        """The configured forge model must actually be SERVED, and answer."""
        if not _configured(self.key):
            return TestResult(ok=False, detail="credentials absent")
        try:
            from ..research import evidence as _ev
            p = await _ev.params()
            async with httpx.AsyncClient(timeout=90) as c:
                m = await c.get("https://openrouter.ai/api/v1/models")
                if m.status_code != 200:
                    return TestResult(ok=False, detail=f"/api/v1/models -> {m.status_code}")
                ids = {x["id"] for x in (m.json() or {}).get("data", [])}
                # BOTH models must resolve. Checking only `forge_model` is how a
                # green contract test coexisted with a verifier that could not
                # make a single call.
                resolved = {}
                for param, default in (("forge_model", "claude-opus-5"),
                                       ("verify_model", "claude-haiku-4-5-20251001")):
                    want = await self._routed(c, p.get(param, default))
                    if want is None or want not in ids:
                        return TestResult(
                            ok=False,
                            detail=f"{param}={p.get(param, default)!r} does not resolve "
                                   f"to a model served here; a wrong id 400s and reads "
                                   f"like a dead key")
                    resolved[param] = want
                want = resolved["forge_model"]
                # 🔴 max_tokens must be generous: an extended-thinking model
                # spends the budget on reasoning first, and a truncated reply
                # comes back as 200 with EMPTY content. At 16 this returns
                # nothing; at 64 it answers. A contract test that trips on its
                # own token budget would mark a working route dormant.
                r = await c.post("https://openrouter.ai/api/v1/chat/completions",
                                 headers=self._h(),
                                 json={"model": want, "max_tokens": 256,
                                       "messages": [{"role": "user",
                                                     "content": "Reply with: ok"}]})
            if r.status_code != 200:
                return TestResult(ok=False, detail=f"completions -> {r.status_code}")
            body = r.json()
            ch = (body.get("choices") or [{}])[0] or {}
            text = ((ch.get("message") or {}).get("content") or "").strip()
            if not text:
                return TestResult(
                    ok=False,
                    detail=f"200 with empty content (finish={ch.get('finish_reason')})")
            return TestResult(ok=True, detail=f"{want} responded",
                              observed_shape={"models": len(ids),
                                              "tokens": (body.get("usage") or {}).get("total_tokens")})
        except Exception as e:                                   # noqa: BLE001
            return TestResult(ok=False, detail=f"{type(e).__name__}: {e}"[:200])


def duckduckgo():
    from ..research.evidence import DuckDuckGoSearch
    return DuckDuckGoSearch()


def _youtube_data_v3():
    # The class lives beside the channel connectors it serves; it is registered
    # HERE because it is a credential-health connector with no `sources` row —
    # the same shape as ollama/qdrant/anthropic, not a discovery source.
    from .sources import YouTubeDataV3
    return YouTubeDataV3


ALL_SERVICES = (OllamaConnector, QdrantConnector, AnthropicConnector,
                OpenRouterConnector, _youtube_data_v3())
