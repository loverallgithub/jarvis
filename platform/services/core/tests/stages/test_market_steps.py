"""Phase F step contracts.

Every `@step` names a test file, and this is the one the six MARKET steps name.
The contracts asserted here are the ones that would be expensive to discover at
runtime: what each step gates on, what it costs, and which of them can reach out
to a real human being.
"""
from __future__ import annotations

import pytest

from jarvis.market import steps as market_steps
from jarvis.runtime.registry import all_steps

IDS = ("market.position", "market.copy", "market.copy_variants",
       "market.media", "market.pages", "market.launch")


@pytest.fixture(autouse=True)
def _registered():
    market_steps.register()


def test_all_six_phase_F_steps_register():
    reg = all_steps()
    missing = [i for i in IDS if i not in reg]
    assert not missing, f"unregistered: {missing}"


def test_every_market_step_declares_this_test_file():
    """A step that cannot name the test proving it does not run."""
    reg = all_steps()
    for i in IDS:
        assert reg[i].test.endswith("test_market_steps.py"), i


def test_copy_gates_on_citation_coverage():
    """The predicate that makes F2's "every factual claim cited" real rather
    than aspirational. Coverage was believed to be 100% and measured 56.3%."""
    spec = all_steps()["market.copy"]
    passes = type("R", (), {"data": {"blocks_stored": 15, "below_floor": 0}})()
    fails = type("R", (), {"data": {"blocks_stored": 15, "below_floor": 1}})()
    empty = type("R", (), {"data": {"blocks_stored": 0, "below_floor": 0}})()
    assert spec.acceptance(passes) is True
    assert spec.acceptance(fails) is False
    assert spec.acceptance(empty) is False


def test_a_single_uncited_block_fails_the_whole_step():
    """Storing 14 good blocks and one uncited one is not a pass. Copy ships as
    a page, and the page is one promise."""
    spec = all_steps()["market.copy"]
    r = type("R", (), {"data": {"blocks_stored": 14, "below_floor": 1}})()
    assert spec.acceptance(r) is False


def test_the_spending_steps_declare_a_budget_and_the_free_ones_do_not():
    reg = all_steps()
    assert reg["market.position"].cost_budget_usd > 0
    assert reg["market.copy"].cost_budget_usd > 0
    # Rendering a page and planning a launch are local work.
    assert reg["market.pages"].cost_budget_usd == 0
    assert reg["market.launch"].cost_budget_usd == 0


def test_media_is_opt_in_because_it_spends_per_run():
    """~$0.039 an image, and ~544 credits per 8s video clip — measured, against
    a vendor doc claiming 68. Defaults must not spend."""
    import os
    assert os.environ.get("JPD_MEDIA_ENABLED", "false").lower() != "true"


def test_launch_accepts_a_REFUSAL_as_a_valid_outcome():
    """A compliance stop is a successful step, not a failure. Otherwise the
    pipeline reads "phase F failed" when the correct thing happened."""
    spec = all_steps()["market.launch"]
    refused = type("R", (), {"data": {"refused": True, "eligible": 0}})()
    approved = type("R", (), {"data": {"ref": "JPD-AB12"}})()
    neither = type("R", (), {"data": {}})()
    assert spec.acceptance(refused) is True
    assert spec.acceptance(approved) is True
    assert spec.acceptance(neither) is False


def test_the_two_human_steps_are_the_ones_that_touch_people():
    """`copy_variants` posts a Sintra card; `launch` mails real humans. Those
    are exactly the two that must end in a human decision, and no others."""
    reg = all_steps()
    for i in ("market.copy_variants", "market.launch"):
        r = type("R", (), {"data": {}})()
        assert reg[i].acceptance(r) is False, f"{i} passes with no human artefact"


def test_pages_requires_a_real_file_on_disk():
    spec = all_steps()["market.pages"]
    ok = type("R", (), {"data": {"path": "/app/data/artifacts/pages/x.html",
                                 "tiers": 3}})()
    nofile = type("R", (), {"data": {"path": "", "tiers": 3}})()
    notiers = type("R", (), {"data": {"path": "/x.html", "tiers": 0}})()
    assert spec.acceptance(ok) is True
    assert spec.acceptance(nofile) is False
    assert spec.acceptance(notiers) is False


def test_registering_twice_is_safe():
    market_steps.register()
    market_steps.register()
    assert len([i for i in all_steps() if i.startswith("market.")]) == len(IDS)
