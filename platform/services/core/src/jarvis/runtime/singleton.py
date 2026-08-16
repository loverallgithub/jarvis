"""Single-owner scheduling via postgres advisory locks.

Pimlico ran its scheduler under `uvicorn --workers 2` and needed a Redis lease
to stop both workers firing every cron. JPD runs `--workers 1` and scales with
replicas — which removes the in-process race but not the cross-replica one: two
core replicas would both run the harvest.

A postgres **session advisory lock** is the right tool here and better than a
Redis lease for this job:

  · it is held by a CONNECTION, so it is released automatically if the process
    dies. A Redis lease with a TTL has a window where a dead owner still holds
    it, and a window where a live owner has lost it without noticing.
  · we already depend on postgres. A scheduler whose correctness depends on a
    second datastore has two ways to fail.

The lock is checked before EVERY tick, not once at startup: an owner that loses
its connection must stop scheduling, not carry on because it won an election
that is no longer true.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

import asyncpg
import structlog

from .. import db

log = structlog.get_logger("runtime.singleton")

# Arbitrary but fixed. Two different jobs must not share a key.
LOCK_KEYS = {
    "health_sweep": 811_001,
    "harvest": 811_002,
    "console_housekeeping": 811_003,
    "scheduler_tick": 811_004,
}


class NotOwner(RuntimeError):
    """Another instance holds this job's lock. Not an error — do nothing."""


@asynccontextmanager
async def hold(job: str) -> AsyncIterator[bool]:
    """Try to hold the lock for `job` for the duration of the block.

    Yields True if we own it, False if someone else does. The caller must check
    — this deliberately does NOT raise, because "another replica is doing it"
    is the normal, healthy case and should not look like a failure.
    """
    key = LOCK_KEYS.get(job)
    if key is None:
        raise ValueError(f"unknown singleton job {job!r}; known: {sorted(LOCK_KEYS)}")

    pool = await db.pool()
    conn: Optional[asyncpg.Connection] = None
    got = False
    try:
        conn = await pool.acquire()
        got = bool(await conn.fetchval("SELECT pg_try_advisory_lock($1)", key))
        if not got:
            log.debug("singleton.not_owner", job=job)
        yield got
    finally:
        if conn is not None:
            if got:
                try:
                    await conn.fetchval("SELECT pg_advisory_unlock($1)", key)
                except Exception:                                # noqa: BLE001
                    # The connection is going back to the pool either way; a
                    # session lock dies with the session, so a failed unlock is
                    # not a leak.
                    pass
            await pool.release(conn)


async def owner_count(job: str) -> int:
    """How many connections hold this lock. Should be 0 or 1, ever.

    Exposed so it can be asserted in a test rather than assumed — a scheduler
    that silently double-fires is the kind of thing nobody notices until the
    bill arrives.
    """
    key = LOCK_KEYS[job]
    return int(await db.fetchval(
        "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND objid=$1",
        key) or 0)
