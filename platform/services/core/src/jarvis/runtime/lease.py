"""Run leases.

Pimlico had a ``lease_owner`` column. It appeared in **no WHERE clause**, so
``KILL`` set a flag that the next tick's ``advance()`` blithely overwrote and
the run came back from the dead. The lesson is not "add a kill flag" — it is
that a lease only means something if every mutation is *conditional on holding
it*.

Every function in this module returns whether it won, and every caller is
required to act on that. ``LeaseLost`` is raised, never logged-and-continued.
"""
from __future__ import annotations

import os
import socket
import uuid

from .. import db
from ..config import settings


class LeaseLost(RuntimeError):
    """We no longer hold the lease we were mutating under.

    This is a normal outcome, not a bug: it means the run was killed, or
    another worker took over after our lease expired. The correct response is
    to stop — NOT to retry, and never to re-assert ownership.
    """


def new_owner() -> str:
    """Owner identity. Host + pid + nonce, so two workers on one host differ."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


async def acquire(run_id: int, owner: str, ttl_s: int | None = None) -> bool:
    """Take the lease if it is free, expired, or already ours."""
    ttl = ttl_s or settings.lease_ttl_s
    got = await db.fetchval(
        """
        UPDATE runs
           SET lease_owner = $1,
               lease_expires_at = now() + make_interval(secs => $3)
         WHERE id = $2
           AND kill_requested = FALSE
           AND (lease_owner IS NULL
                OR lease_owner = $1
                OR lease_expires_at IS NULL
                OR lease_expires_at < now())
        RETURNING id
        """,
        owner, run_id, float(ttl))
    return got is not None


async def renew(run_id: int, owner: str, ttl_s: int | None = None) -> bool:
    ttl = ttl_s or settings.lease_ttl_s
    got = await db.fetchval(
        """
        UPDATE runs
           SET lease_expires_at = now() + make_interval(secs => $3)
         WHERE id = $2 AND lease_owner = $1 AND kill_requested = FALSE
        RETURNING id
        """,
        owner, run_id, float(ttl))
    return got is not None


async def release(run_id: int, owner: str) -> bool:
    got = await db.fetchval(
        """
        UPDATE runs SET lease_owner = NULL, lease_expires_at = NULL
         WHERE id = $2 AND lease_owner = $1
        RETURNING id
        """,
        owner, run_id)
    return got is not None


async def held_by(run_id: int, owner: str) -> bool:
    return bool(await db.fetchval(
        "SELECT 1 FROM runs WHERE id = $1 AND lease_owner = $2 "
        "AND kill_requested = FALSE AND lease_expires_at > now()",
        run_id, owner))


async def request_kill(run_id: int) -> bool:
    """Ask a run to stop.

    Sets the flag AND clears the lease, so the running worker's next guarded
    mutation fails and it stops of its own accord. Nothing else needs to
    cooperate — that is the point.
    """
    got = await db.fetchval(
        "UPDATE runs SET kill_requested = TRUE, lease_owner = NULL, "
        "status = 'killed', ended_at = now() WHERE id = $1 RETURNING id",
        run_id)
    return got is not None
