"""Test fixtures.

Integration tests run against a REAL postgres, with the REAL migrations
applied. They are not mocked, on purpose: every invariant this suite protects
is enforced by a database constraint or a WHERE clause, and a mock would
happily let all of them through.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

TEST_DSN = os.environ.get(
    "JPD_TEST_PG_DSN", "postgresql://jarvis:jarvis@127.0.0.1:5632/jarvis_test")

# ---------------------------------------------------------------------------
# 🔴 REFUSE TO RUN AGAINST ANYTHING THAT IS NOT OBVIOUSLY A TEST DATABASE.
#
# `clean_db` issues TRUNCATE ... CASCADE. A suite that can be pointed at the
# production database by a stray environment variable is one typo away from
# destroying the run history, the evidence and the order book. The guard is
# crude on purpose — a name-based check cannot be argued with, and there is
# deliberately no override flag.
# ---------------------------------------------------------------------------
_db_name = TEST_DSN.rsplit("/", 1)[-1].split("?")[0]
if "test" not in _db_name.lower():
    raise RuntimeError(
        f"refusing to run: the target database is {_db_name!r}, which is not "
        f"recognisably a test database. This suite TRUNCATES tables. Point "
        f"JPD_TEST_PG_DSN at a database whose name contains 'test'.")

os.environ["JPD_PG_DSN"] = TEST_DSN
os.environ.setdefault("JPD_PACKAGE_ROOT", str(ROOT))

_migrated = False

# Reference tables seeded by the migrations. Tests legitimately mutate these —
# retuning a gate or a price ratio is an UPDATE by design — so they must be
# restored between tests or one test's retune silently reprices another's
# ladder. Snapshotting from the MIGRATION rather than hardcoding the values
# here means the fixture can never drift from what actually ships.
#
# All three have natural text primary keys and no serial ids, so delete+restore
# is safe; tables with serial ids referenced elsewhere are deliberately absent.
_SEEDED_TABLES = ("pricing_policy", "gate_thresholds", "job_registry",
                  "telegram_streams", "notification_channels")
_SNAPSHOT: dict[str, list[dict]] = {}


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: needs a live postgres")


async def _snapshot_seeded(db):
    for t in _SEEDED_TABLES:
        rows = await db.fetch(f"SELECT * FROM {t}")
        _SNAPSHOT[t] = [dict(r) for r in rows]


async def _restore_seeded(db):
    for t in _SEEDED_TABLES:
        rows = _SNAPSHOT.get(t) or []
        if not rows:
            continue
        await db.execute(f"DELETE FROM {t}")
        cols = list(rows[0].keys())
        placeholders = ", ".join(f"${i+1}" for i in range(len(cols)))
        sql = f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({placeholders})"
        for r in rows:
            await db.execute(sql, *[r[c] for c in cols])


@pytest.fixture
async def clean_db():
    """A migrated, empty database, with a connection pool bound to THIS test's
    event loop.

    The pool is created and closed per test on purpose. asyncpg binds its
    connections to the loop that created them, so a session-scoped pool shared
    with function-scoped tests fails with "attached to a different loop" — an
    error that reads like a database problem and is not one. Per-test pools
    cost a few milliseconds and remove the whole class of confusion.
    """
    from jarvis import db

    global _migrated
    if not _migrated:
        # 🔴 Rebuild the schema from the migrations, every session.
        #
        # Without this the suite inherits whatever the PREVIOUS run left
        # behind. That is not hypothetical: a test that legitimately deletes a
        # pricing_policy row (proving the ladder refuses to guess) left the
        # table with two rows, and the next run's "snapshot the seeded tables"
        # faithfully captured the damage and restored it before every test.
        # Four tests failed for a reason that had nothing to do with their code.
        #
        # A suite that gates deploys must be reproducible from nothing.
        await db.execute("DROP SCHEMA public CASCADE")
        await db.execute("CREATE SCHEMA public")
        await db.close()                     # drop pooled conns bound to old oids
        await db.migrate()
        await _snapshot_seeded(db)
        _migrated = True

    # RESTART IDENTITY so ids are predictable; CASCADE because the FK graph is
    # real and we want it exercised rather than worked around.
    # NOTE `signals`, `clusters`, `gate_evaluations` were added after harvest
    # tests started interfering: signals persist across tests keyed on
    # (source_id, external_id), so a second test harvesting the same fake data
    # stored ZERO and looked like a broken persister. `sources` is deliberately
    # NOT here — it is seeded reference data, restored by _restore_seeded.
    await db.execute(
        "TRUNCATE runs, steps, checkpoints, human_tasks, watermarks, dead_letter, "
        "needs, solutions, artifacts, evidence, claims, offers, orders, "
        "entitlements, fulfilments, voices, voice_mentions, delivery_tokens, "
        "notifications, provider_events, attributions, upgrades, "
        "signals, clusters, gate_evaluations, telegram_replies, alert_synthetics "
        "RESTART IDENTITY CASCADE")
    await db.execute(
        "UPDATE connector_health SET state = 'dormant', fail_streak = 0, "
        "zero_yield_streak = 0")
    await _restore_seeded(db)
    try:
        yield db
    finally:
        await db.close()
