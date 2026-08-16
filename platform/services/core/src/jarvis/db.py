"""Postgres access and the migration runner.

The migration runner records the sha256 of each applied file. If a file that
has already been applied changes on disk, the runner **refuses to proceed**
rather than silently diverging — this host has demonstrated that source files
can revert without explanation (C8), so "the file on disk is what ran" is an
assumption we do not make.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Optional

import asyncpg

from .config import settings

_pool: Optional[asyncpg.Pool] = None

MIGRATIONS_DIR = Path(settings.package_root) / "migrations"


def dsn() -> str:
    """Resolved at CALL time, not import time.

    ``jpd --dsn ...`` sets the environment after this module is imported, and
    a DSN frozen at import would silently point the CLI at the wrong database
    while appearing to honour the flag.
    """
    return os.environ.get("JPD_PG_DSN", "").strip() or settings.pg_dsn


async def pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn(), min_size=1, max_size=10, command_timeout=60,
        )
    return _pool


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def fetch(q: str, *args: Any) -> list[asyncpg.Record]:
    p = await pool()
    async with p.acquire() as c:
        return await c.fetch(q, *args)


async def fetchrow(q: str, *args: Any) -> Optional[asyncpg.Record]:
    p = await pool()
    async with p.acquire() as c:
        return await c.fetchrow(q, *args)


async def fetchval(q: str, *args: Any) -> Any:
    p = await pool()
    async with p.acquire() as c:
        return await c.fetchval(q, *args)


async def execute(q: str, *args: Any) -> str:
    p = await pool()
    async with p.acquire() as c:
        return await c.execute(q, *args)


# ---------------------------------------------------------------------------
# migrations
# ---------------------------------------------------------------------------

_MIGRATION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    sha256     TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


class MigrationDrift(RuntimeError):
    """An already-applied migration file has changed on disk.

    Not recoverable automatically. Either the file reverted (C8) or someone
    edited a shipped migration. Both need a human to look, and neither should
    be papered over by re-running it.
    """


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def migration_files() -> list[Path]:
    if not MIGRATIONS_DIR.is_dir():
        return []
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


async def migrate(dry_run: bool = False) -> list[dict]:
    """Apply pending migrations. Idempotent. Returns a per-file report."""
    p = await pool()
    report: list[dict] = []
    async with p.acquire() as c:
        await c.execute(_MIGRATION_TABLE)
        applied = {r["version"]: r["sha256"] for r in
                   await c.fetch("SELECT version, sha256 FROM schema_migrations")}

        for f in migration_files():
            version = f.name
            digest = _sha256_file(f)

            if version in applied:
                if applied[version] != digest:
                    raise MigrationDrift(
                        f"{version} was applied with sha256={applied[version][:12]} "
                        f"but the file on disk is now {digest[:12]}. Refusing to continue. "
                        f"Write a new migration; never edit a shipped one.")
                report.append({"version": version, "action": "already_applied"})
                continue

            if dry_run:
                report.append({"version": version, "action": "would_apply"})
                continue

            # Each migration runs in its own transaction. A failure leaves the
            # earlier migrations applied and this one absent — which is exactly
            # what the version table should then say.
            async with c.transaction():
                await c.execute(f.read_text())
                await c.execute(
                    "INSERT INTO schema_migrations (version, sha256) VALUES ($1, $2)",
                    version, digest)
            report.append({"version": version, "action": "applied"})

    return report


async def schema_ready() -> bool:
    """True if the runtime tables the engine needs exist."""
    required = ("runs", "steps", "checkpoints", "connector_health", "human_tasks")
    got = await fetchval(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = ANY($1::text[])",
        list(required))
    return int(got or 0) == len(required)
