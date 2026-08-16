"""The card and its task row must agree on the reference.

This is a small bug with a nasty shape. `human.sintra` pre-renders a card so it
can print the Sintra-specific layout, and that card carries
`VERIFY  jpd tasks show SIN-ABC123`. If `tasks.create` then generates its own
reference, the operator is told to run a command that finds nothing — and the
symptom ("task not found") points at the task store rather than at the card
builder.

Caught by a test, not by review.
"""
from __future__ import annotations

from jarvis import db
from jarvis.console import cards, human


async def test_the_sintra_card_ref_matches_its_row(clean_db):
    resp = await human.sintra(key="k1", why="blocking copy variant B",
                              bot="Aria", prompt="write ad copy", min_chars=50)
    row = await db.fetchrow("SELECT ref, verify_command FROM human_tasks WHERE ref=$1",
                            resp.ref)
    assert row is not None, "the ref the caller was given must exist in the table"
    assert row["ref"].startswith("SIN-")
    # The command printed on the card must be runnable as printed.
    assert row["verify_command"] == f"jpd tasks show {row['ref']}"


async def test_the_decision_card_ref_matches_its_row(clean_db):
    resp = await human.decide(key="k2", question="Publish?", why="irreversible",
                              options=["approve", "reject"])
    row = await db.fetchrow("SELECT ref FROM human_tasks WHERE ref=$1", resp.ref)
    assert row is not None
    assert row["ref"].startswith("DEC-")


async def test_a_plain_task_still_gets_a_generated_ref(clean_db):
    from jarvis.console import tasks
    t = await tasks.create(type="task", title="t", why="w",
                           reply_schema={"type": "text", "min_chars": 1})
    assert t.ref.startswith("JPD-")


def test_cards_escape_html_so_a_prompt_cannot_break_the_message():
    """Cards are sent with parse_mode=HTML. An unescaped `<` in generated copy
    would make Telegram reject the whole message — and the card is the only
    thing telling the operator a run is blocked."""
    card = cards.sintra(ref="SIN-1", why="a <b>bold</b> claim", bot="Aria",
                        prompt="use <angle> brackets & ampersands",
                        verify_command="jpd tasks show SIN-1")
    assert "&lt;angle&gt;" in card
    assert "&amp;" in card
    # Our own intended markup survives.
    assert "<b>SINTRA TASK</b>" in card


def test_the_decision_card_lists_every_option_and_the_skip_route():
    card = cards.decision(ref="DEC-1", question="Publish?", why="irreversible",
                          options=["approve", "reject"], context={"price": "€297"})
    assert "approve" in card and "reject" in card
    assert "SKIP" in card
    assert "€297" in card
