"""Registry refusals.

These tests protect rule 6: a step cannot be registered without a test that
exists. It is the mechanism that makes "every step is tested" structurally
true instead of aspirational — and Pimlico is the proof that aspiration is
not enough. It shipped eleven alert rules; four of those detectors are
silently broken today.
"""
from __future__ import annotations

import pytest

from jarvis.runtime import registry
from jarvis.runtime.registry import StepDefinitionError, step
from jarvis.runtime.types import StepResult, StepStatus

THIS_TEST = "tests/unit/test_registry.py"


@pytest.fixture(autouse=True)
def _clean():
    registry._reset_for_tests()
    yield
    registry._reset_for_tests()


def test_step_registers_with_an_existing_test_file():
    @step(id="t.ok", phase="TEST", acceptance=lambda r: True, test=THIS_TEST)
    async def _s(ctx):
        return StepResult.ok()

    assert "t.ok" in registry.all_steps()
    assert registry.validate_registry() == []


def test_step_refuses_a_missing_test_file():
    """The whole point. A named-but-absent test is the same as no test."""
    with pytest.raises(StepDefinitionError, match="does not exist"):
        @step(id="t.notest", phase="TEST", acceptance=lambda r: True,
              test="tests/unit/test_this_was_never_written.py")
        async def _s(ctx):
            return StepResult.ok()

    assert "t.notest" not in registry.all_steps()


def test_step_refuses_a_synchronous_function():
    """A blocking step holds the event loop and outlives its lease.

    Pimlico's clustering ran 181s inline for 1,690 signals, froze HTTP, and
    the lease expired underneath it.
    """
    with pytest.raises(StepDefinitionError, match="must be async"):
        @step(id="t.sync", phase="TEST", acceptance=lambda r: True, test=THIS_TEST)
        def _s(ctx):
            return StepResult.ok()


def test_step_refuses_duplicate_ids():
    @step(id="t.dup", phase="TEST", acceptance=lambda r: True, test=THIS_TEST)
    async def _a(ctx):
        return StepResult.ok()

    with pytest.raises(StepDefinitionError, match="duplicate"):
        @step(id="t.dup", phase="TEST", acceptance=lambda r: True, test=THIS_TEST)
        async def _b(ctx):
            return StepResult.ok()


def test_acceptance_that_raises_is_a_failure_not_a_pass():
    """A predicate that throws must NOT be treated as truthy.

    Swallowing an exception into a default is how a verification step comes to
    approve everything it was built to catch.
    """
    def explodes(r):
        raise ValueError("boom")

    @step(id="t.raises", phase="TEST", acceptance=explodes, test=THIS_TEST)
    async def _s(ctx):
        return StepResult.ok()

    spec = registry.get("t.raises")
    ok, reason = spec.evaluate_acceptance(StepResult.ok())
    assert ok is False
    assert "ValueError" in reason and "boom" in reason


def test_acceptance_description_is_captured_for_the_failure_message():
    @step(id="t.desc", phase="TEST",
          acceptance=lambda r: len(r.evidence) >= 5, test=THIS_TEST)
    async def _s(ctx):
        return StepResult.ok()

    ok, reason = registry.get("t.desc").evaluate_acceptance(StepResult.ok())
    assert ok is False
    # The reason must say what was expected. "acceptance failed" alone costs
    # an hour of reading source at 3am.
    assert "len(r.evidence) >= 5" in reason


def test_validate_registry_notices_a_vanished_test(tmp_path, monkeypatch):
    """C8: files on this host have reverted without explanation.

    Registration-time checking is not enough if the file can disappear
    afterwards, so startup re-validates.
    """
    t = tmp_path / "test_temp.py"
    t.write_text("# placeholder\n")

    @step(id="t.vanish", phase="TEST", acceptance=lambda r: True, test=str(t))
    async def _s(ctx):
        return StepResult.ok()

    assert registry.validate_registry() == []
    t.unlink()
    problems = registry.validate_registry()
    assert any("vanished" in p for p in problems)
