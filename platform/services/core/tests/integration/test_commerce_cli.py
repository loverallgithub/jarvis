"""The `jpd commerce` commands.

These exist because HT-005 tells the operator to run them. A runbook that cites
a command which does not exist, or which crashes on an empty database, is worse
than no runbook — it is followed at the exact moment things are least certain.

Every command here must work with NOTHING in the database.
"""
from __future__ import annotations

import pytest

from jarvis import db
from jarvis.cli import (cmd_commerce_contract_test, cmd_commerce_orders,
                        cmd_commerce_status, cmd_commerce_sweep,
                        cmd_commerce_test_ladder)


class _A:
    last = 10
    base_minor = 100
    label = "test"


async def test_status_works_on_an_empty_database(clean_db, capsys):
    rc = await cmd_commerce_status(_A())
    out = capsys.readouterr().out
    assert rc == 0
    assert "no ladder has been created" in out
    assert "must be zero" in out


async def test_orders_works_on_an_empty_database(clean_db, capsys):
    assert await cmd_commerce_orders(_A()) == 0
    assert "no orders" in capsys.readouterr().out


async def test_sweep_works_on_an_empty_database(clean_db, capsys):
    assert await cmd_commerce_sweep(_A()) == 0
    assert "missing 0" in capsys.readouterr().out.replace("\x1b[32m", "").replace("\x1b[0m", "")


async def test_status_flags_an_undelivered_paid_order(clean_db, capsys):
    """"Which buyers paid and did not receive" is the single most important
    question this system can be asked. It must be one command, and it must
    exit non-zero."""
    need = await db.fetchval("INSERT INTO needs (title) VALUES ('n') RETURNING id")
    sol = await db.fetchval(
        "INSERT INTO solutions (need_id, title) VALUES ($1,'s') RETURNING id", need)
    off = await db.fetchval(
        "INSERT INTO offers (solution_id, tier, price_minor, live) "
        "VALUES ($1,'roadmap',29700,TRUE) RETURNING id", sol)
    order = await db.fetchval(
        "INSERT INTO orders (offer_id, buyer_ref, buyer_email, amount_minor, currency, "
        "provider, provider_ref, signature_valid, amount_matched, status) "
        "VALUES ($1,'b','b@x.test',29700,'EUR','stub','t1',TRUE,TRUE,'verified') RETURNING id",
        off)
    ent = await db.fetchval(
        "INSERT INTO entitlements (order_id, buyer_ref, solution_id, tier) "
        "VALUES ($1,'b',$2,'roadmap') RETURNING id", order, sol)
    await db.execute(
        "INSERT INTO fulfilments (entitlement_id, status, tier, error) "
        "VALUES ($1,'failed','roadmap','artifact missing')", ent)

    rc = await cmd_commerce_status(_A())
    assert rc == 1, "an undelivered paid order must make this command fail"
    assert f"order {order}" in capsys.readouterr().out


async def test_test_ladder_refuses_while_the_provider_is_dormant(clean_db, capsys):
    """It must not create real products against a provider that has not passed
    a contract test — and it must say which human task is outstanding."""
    rc = await cmd_commerce_test_ladder(_A())
    out = capsys.readouterr().out
    assert rc == 1
    assert "not live" in out
    assert "HT-005" in out
    assert await db.fetchval("SELECT count(*) FROM offers") == 0


async def test_contract_test_reports_dormant_without_credentials(clean_db, capsys):
    rc = await cmd_commerce_contract_test(_A())
    out = capsys.readouterr().out
    assert rc == 1
    assert "dormant" in out
    assert await db.fetchval(
        "SELECT state FROM connector_health WHERE connector='ghl_payments'") == "dormant"
