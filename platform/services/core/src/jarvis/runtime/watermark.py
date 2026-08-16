"""Clamped watermarks.

Pimlico's n8n failure watcher stored ``last_seen_execution = 1757`` — a
high-water mark from a *different* n8n instance — against a local maximum id
of 34. The predicate ``id > last_seen`` was therefore unsatisfiable forever,
and the watcher reported "no failures" every day while WF05 was erroring. It
was blind for weeks and nothing said so.

The fix is one line of arithmetic applied at every read: a cursor may never
exceed the highest value actually observed from the backend it points at.

    effective = min(saved, observed_max)

This makes an instance swap, a restore-from-backup, or a manual poke
self-healing instead of permanently blinding. The clamp is **recorded**, not
silent — a cursor that clamps is telling you something changed underneath it.
"""
from __future__ import annotations

import structlog

from .. import db

log = structlog.get_logger()


async def read(name: str, observed_max: int) -> int:
    """Return the effective cursor, clamped to what the backend actually has.

    ``observed_max`` is the highest id/timestamp visible in the CURRENT
    response. Pass 0 when the backend returned nothing — an empty response is
    not evidence that the backend reset, so no clamp is applied.
    """
    row = await db.fetchrow(
        "SELECT value, observed_max FROM watermarks WHERE name = $1", name)
    saved = int(row["value"]) if row else 0

    if observed_max <= 0 or saved <= observed_max:
        return saved

    log.warning("watermark.clamped", name=name, was=saved, clamped_to=observed_max,
                reason="saved cursor exceeds highest observed value — backend reset "
                       "or instance swap")
    await db.execute(
        """
        INSERT INTO watermarks (name, value, observed_max, clamped_at, updated_at)
        VALUES ($1, $2, $2, now(), now())
        ON CONFLICT (name) DO UPDATE
          SET value = $2, observed_max = $2, clamped_at = now(), updated_at = now()
        """,
        name, observed_max)
    return observed_max


async def advance(name: str, value: int, observed_max: int | None = None) -> int:
    """Move the cursor forward. Never backwards, never past what was observed."""
    ceiling = observed_max if observed_max is not None else value
    target = min(int(value), int(ceiling))
    new = await db.fetchval(
        """
        INSERT INTO watermarks (name, value, observed_max, updated_at)
        VALUES ($1, $2, $3, now())
        ON CONFLICT (name) DO UPDATE
          SET value = GREATEST(watermarks.value, EXCLUDED.value),
              observed_max = GREATEST(watermarks.observed_max, EXCLUDED.observed_max),
              updated_at = now()
        RETURNING value
        """,
        name, target, int(ceiling))
    return int(new)


async def reset(name: str) -> None:
    await db.execute("DELETE FROM watermarks WHERE name = $1", name)
