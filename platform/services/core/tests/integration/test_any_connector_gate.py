"""`requires_any_connectors` — one live route out of several is enough.

🔴 Why this exists. On 2026-08-08 the Anthropic key hit a spend cap, an
OpenRouter fallback was wired into `_llm`, it was verified working in
production — and `jpd forge run 13` still returned:

    forge.generate    skipped_dormant
        connector not live: anthropic

The step gate is on connector HEALTH, and it only knew about one provider. **A
fallback the health system cannot see is a fallback the engine will refuse to
use.**

The fix must not weaken the gate. `_connector_gate` is the single check that
kills the Sintra class of failure — connector broken, code carried on, failure
text published. So: `requires_connectors` keeps AND semantics, the new
`requires_any_connectors` adds OR, and an empty or all-dormant set still fails.
"""
from __future__ import annotations

import pytest

from jarvis.connectors import base as connectors
from jarvis.runtime import registry
from jarvis.runtime.engine import _connector_gate
from jarvis.runtime.registry import step
from jarvis.runtime.types import StepResult

THIS = "tests/integration/test_any_connector_gate.py"


@pytest.fixture(autouse=True)
def _clean():
    registry._reset_for_tests()
    yield
    registry._reset_for_tests()


def _spec(step_id: str, **kw):
    @step(id=step_id, phase="TEST", acceptance=lambda r: True, test=THIS, **kw)
    async def _s(ctx):
        return StepResult.ok()
    return registry.get(step_id)


async def _live(name: str):
    # `clean_db` truncates connector_health, and a connector with no row is
    # dormant by design — so register before granting live, exactly as
    # `registry.register_all()` does at startup.
    await connectors.register(name, "api", "test connector")
    await connectors.record_contract_test(name, True, "shape ok")


# ---------------------------------------------------------------------------
# ANY-of
# ---------------------------------------------------------------------------

async def test_one_live_route_of_two_satisfies_the_gate(clean_db):
    """The forge case: anthropic capped, openrouter working."""
    spec = _spec("t.any", requires_any_connectors=("anthropic", "openrouter"))
    await _live("openrouter")
    assert await _connector_gate(spec) is None


async def test_the_other_one_also_satisfies_it(clean_db):
    spec = _spec("t.any2", requires_any_connectors=("anthropic", "openrouter"))
    await _live("anthropic")
    assert await _connector_gate(spec) is None


async def test_all_dormant_still_blocks(clean_db):
    """🔴 The gate must NOT be relaxed into 'no requirement'. With no working
    provider the step must not run, must not fabricate, must not persist."""
    spec = _spec("t.none", requires_any_connectors=("anthropic", "openrouter"))
    blocked = await _connector_gate(spec)
    assert blocked == "anthropic or openrouter", \
        "the message must name every route that was tried"


async def test_unregistered_connectors_are_dormant_not_live(clean_db):
    """Absent is the safe direction — the rule the AND gate already had."""
    spec = _spec("t.ghost", requires_any_connectors=("nonexistent_a", "nonexistent_b"))
    assert await _connector_gate(spec) is not None


async def test_a_degraded_route_does_not_satisfy_the_gate(clean_db):
    """`degraded` means failing-but-not-written-off. It is not `live`, and a
    step that costs real money must not run on a route we already distrust."""
    await _live("openrouter")
    for _ in range(2):
        await connectors.record_probe("openrouter", False, "timeout")
    assert await connectors.state_of("openrouter") == "degraded"

    spec = _spec("t.degraded", requires_any_connectors=("anthropic", "openrouter"))
    assert await _connector_gate(spec) is not None


# ---------------------------------------------------------------------------
# AND-of must be unchanged
# ---------------------------------------------------------------------------

async def test_requires_connectors_still_means_ALL(clean_db):
    spec = _spec("t.all", requires_connectors=("anthropic", "openrouter"))
    await _live("anthropic")
    assert await _connector_gate(spec) == "openrouter", "AND semantics must survive"
    await _live("openrouter")
    assert await _connector_gate(spec) is None


async def test_both_kinds_compose(clean_db):
    """A step may require a specific connector AND any-one-of a set."""
    spec = _spec("t.mix", requires_connectors=("duckduckgo",),
                 requires_any_connectors=("anthropic", "openrouter"))
    await _live("openrouter")
    assert await _connector_gate(spec) == "duckduckgo", "the AND part still binds"
    await _live("duckduckgo")
    assert await _connector_gate(spec) is None


async def test_no_requirements_means_no_gate(clean_db):
    assert await _connector_gate(_spec("t.free")) is None


# ---------------------------------------------------------------------------
# the forge wiring itself
# ---------------------------------------------------------------------------

async def test_forge_generate_accepts_either_llm_route(clean_db):
    """Guards the actual defect: forge.generate must not be pinned to one
    provider. If this reverts, a capped key strands the forge again."""
    registry._reset_for_tests()
    from jarvis.forge import steps as fsteps
    fsteps.register()

    spec = registry.get("forge.generate")
    assert set(spec.requires_any_connectors) == {"anthropic", "openrouter"}
    assert "anthropic" not in spec.requires_connectors, \
        "pinning to anthropic alone is what caused skipped_dormant on 2026-08-08"

    await _live("openrouter")
    assert await _connector_gate(spec) is None
