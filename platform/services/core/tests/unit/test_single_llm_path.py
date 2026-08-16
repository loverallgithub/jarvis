"""There must be exactly ONE LLM path, and it must be the one with the fallback.

WHAT WENT WRONG
───────────────
`research/dossier.py` maintained its own Anthropic-only `_llm`. When the
Anthropic spend cap landed, the OpenRouter fallback was added to
`forge/build.py` — and only there.

Every research call has returned None ever since. Gap analysis extracted
nothing, willingness-to-pay found nothing, and NO ERROR REACHED THE OPERATOR,
because returning None on failure is that function's documented contract. A
silent None is indistinguishable from "the model had nothing to say".

Found 2026-08-09 when `jpd research solution` produced zero queries and blamed
missing positioning — positioning that was present and correct. The misleading
message is the tell: a second copy of a failure path does not just double the
maintenance, it makes the symptom point at the wrong thing.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "jarvis"


def _sources() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_only_the_shared_path_and_the_health_probe_call_anthropic_directly():
    """A second WORKING caller is a second thing to forget when the credential,
    the model or the fallback changes.

    `connectors/services.py` is the deliberate exception: its job is to probe
    Anthropic and report whether the connector is live. It must call the real
    endpoint — routing the health check through a fallback would make it report
    "live" while Anthropic was refusing every request, which is precisely the
    state the cap put us in.
    """
    PROBE_ALLOWED = "connectors/services.py"
    callers = [p.relative_to(SRC).as_posix() for p in _sources()
               if "api.anthropic.com/v1/messages" in p.read_text()]
    assert sorted(callers) == sorted([PROBE_ALLOWED, "forge/build.py"]), (
        f"unexpected direct Anthropic callers: {callers}. Work goes through "
        f"forge.build._llm, which has the OpenRouter fallback; only the health "
        f"connector probes the provider directly.")


def test_research_llm_delegates_rather_than_reimplementing():
    from jarvis.research import dossier
    src = inspect.getsource(dossier._llm)
    assert "_shared_llm" in src or "build import _llm" in src
    assert "api.anthropic.com" not in src


@pytest.mark.asyncio
async def test_research_llm_reaches_the_shared_path(monkeypatch):
    """Delegation must actually be wired, not merely written."""
    from jarvis.forge import build
    from jarvis.research import dossier

    seen = {}

    async def fake(prompt, *, max_tokens=2000, model_param="forge_model"):
        seen["model_param"] = model_param
        seen["max_tokens"] = max_tokens
        return "ok"

    monkeypatch.setattr(build, "_llm", fake)
    out = await dossier._llm("hello", max_tokens=400)
    assert out == "ok"
    assert seen["max_tokens"] == 400
    # research keeps its own tunable model, independent of the forge
    assert seen["model_param"] == "llm_model"


def test_the_fallback_exists_on_the_shared_path():
    from jarvis.forge import build
    src = inspect.getsource(build._llm)
    assert "_via_openrouter" in src, (
        "the shared path lost its fallback — every phase now depends on a "
        "single capped provider")


def test_no_module_defines_a_second_private_llm():
    """One definition, one place to fix."""
    defs = [p.relative_to(SRC).as_posix() for p in _sources()
            if re.search(r"^async def _llm\(", p.read_text(), re.M)]
    assert sorted(defs) == ["forge/build.py", "research/dossier.py"], defs
    # dossier's is permitted ONLY because it delegates — asserted above.
