"""The Telegram command surface — parsing, dispatch, and the SPEND BOUNDARY.

The most important test in this file is `test_no_command_can_spend_or_deploy`.
A Telegram group is a shared surface: anyone in it, a forwarded message, or one
compromised account can send a line of text. So the rule is enforced in code and
asserted here, not left to reviewer memory — the command table IS the boundary.
"""
from __future__ import annotations

import pytest

from jarvis.console import commands


# ── parsing ────────────────────────────────────────────────────────────────
def test_plain_text_is_not_a_command():
    assert commands.parse("just a reply to a card") is None
    assert commands.parse("") is None
    assert commands.parse("   ") is None


def test_a_bare_slash_is_not_a_command():
    assert commands.parse("/") is None


def test_the_botname_suffix_is_stripped():
    """Telegram appends @botname in GROUPS — exactly where commands are used.

    Without stripping it, every command would fail in the only place it runs.
    """
    assert commands.parse("/status@jpd_com_bot") == ("status", [])
    assert commands.parse("/skip@jpd_com_bot JPD-1 busy") == ("skip", ["JPD-1", "busy"])


def test_arguments_are_split_and_case_is_normalised():
    assert commands.parse("/SKIP JPD-AB12 out of scope") == (
        "skip", ["JPD-AB12", "out", "of", "scope"])


def test_leading_and_trailing_space_is_tolerated():
    assert commands.parse("  /status  ") == ("status", [])


# ── the boundary ───────────────────────────────────────────────────────────
def test_no_command_can_spend_or_deploy():
    """The whole point of the read-only + safe-action split.

    If a future change adds /forge-run, /research or /deploy, this fails and the
    author has to justify it deliberately rather than by omission.
    """
    forbidden = {"deploy", "forge-run", "forgerun", "research", "run", "build",
                 "generate", "publish", "sell", "pay", "refund", "migrate",
                 "rollback", "kill", "delete", "drop"}
    assert forbidden.isdisjoint(commands.HANDLERS)


def test_the_command_table_is_exactly_what_was_agreed():
    assert set(commands.HANDLERS) == {
        # read-only
        "status", "tasks", "connectors", "discover", "forge", "sources",
        "streams", "checkpoint", "alerts", "help", "start",
        # safe actions — no spend, no deploy
        "skip", "expire", "harvest", "verify",
    }


def test_forge_is_a_READ_command_not_a_run_command():
    """`/forge` reports artifacts. It must never trigger a forge RUN, which
    costs $6-9 of LLM budget per invocation."""
    import inspect
    src = inspect.getsource(commands.cmd_forge)
    assert "SELECT" in src
    for spender in ("build_tier", "forge.run", "generate_section", "_llm("):
        assert spender not in src


# ── dispatch ───────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_non_commands_return_None_so_replies_still_reach_tasks():
    """None means 'not a command' — the poller must fall through to reply
    matching, or answering a card would stop working."""
    assert await commands.dispatch("here is my answer") is None


@pytest.mark.asyncio
async def test_an_unknown_command_is_answered_not_ignored():
    """Silence is indistinguishable from a dead bot."""
    out = await commands.dispatch("/nonsense")
    assert out is not None and "Unknown command" in out


@pytest.mark.asyncio
async def test_help_lists_commands_and_names_the_boundary():
    out = await commands.dispatch("/help")
    assert "/status" in out and "/skip" in out
    assert "deploy" in out.lower()          # states what is deliberately absent


@pytest.mark.asyncio
async def test_skip_without_a_reason_is_refused():
    out = await commands.dispatch("/skip JPD-AB12")
    assert "usage" in out.lower()


@pytest.mark.asyncio
async def test_a_handler_that_raises_becomes_a_message_not_a_crash():
    """The poll loop must survive a bad command — it is what reports outages."""
    async def boom(_args):
        raise RuntimeError("kaboom")

    commands.HANDLERS["boomtest"] = boom
    try:
        out = await commands.dispatch("/boomtest")
        assert "failed" in out and "RuntimeError" in out
        assert "kaboom" not in out          # internals are not leaked to a chat
    finally:
        del commands.HANDLERS["boomtest"]


@pytest.mark.asyncio
async def test_replies_are_clipped_below_the_telegram_limit():
    async def huge(_args):
        return "x" * 99_000

    commands.HANDLERS["hugetest"] = huge
    try:
        out = await commands.dispatch("/hugetest")
        assert len(out) <= 4096
        assert "truncated" in out
    finally:
        del commands.HANDLERS["hugetest"]


def test_html_is_escaped_so_a_malformed_entity_cannot_kill_the_send():
    """parse_mode=HTML means Telegram REJECTS the whole message on a stray '<'."""
    assert commands._esc("<script>&") == "&lt;script&gt;&amp;"
