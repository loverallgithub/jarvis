"""The CLI is the operator's only hand on the money path — so it must exist.

`jpd commerce publish` was added on 2026-08-09. Before it, `offers.publish` had
exactly one caller: `test-ladder`, which creates a THROWAWAY €1 ladder and
publishes it as a side effect. There was no way to publish a real ladder except
by reaching into the container with a raw python exec — an unlogged, unreviewed
action on the one code path that takes money.

These tests assert the command is reachable from argv. They do not exercise the
gate itself; that is the journey suite's job (`test_buyer_journeys.py`), which
runs against a real database.
"""
from __future__ import annotations

import pytest

from jarvis.cli import build_parser


def _parse(*argv):
    return build_parser().parse_args(list(argv))


def test_publish_is_reachable_from_argv():
    args = _parse("commerce", "publish", "11")
    assert args.solution_id == "11"
    from jarvis.cli import cmd_commerce_publish
    assert args.fn is cmd_commerce_publish


def test_publish_requires_a_solution():
    """Publishing "whatever is lying around" is not a thing an operator should
    be able to typo their way into."""
    with pytest.raises(SystemExit):
        _parse("commerce", "publish")


def test_publish_did_not_displace_the_other_commerce_commands():
    """A subparser added carelessly can shadow its siblings."""
    from jarvis.cli import (cmd_commerce_orders, cmd_commerce_status,
                            cmd_commerce_test_ladder)
    assert _parse("commerce", "status").fn is cmd_commerce_status
    assert _parse("commerce", "orders").fn is cmd_commerce_orders
    assert _parse("commerce", "test-ladder").fn is cmd_commerce_test_ladder


def test_test_ladder_still_defaults_to_ONE_EURO():
    """It creates REAL products with REAL money. The default anchor must stay
    at the smallest amount that proves the path, never inherit a real price."""
    assert _parse("commerce", "test-ladder").base_minor == 100
