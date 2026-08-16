"""Connector registry — implementations bound to registry rows.

Two halves that must agree:
  · `sources` (a DB table) says which connectors EXIST and what type they are
  · this module says which of them have an IMPLEMENTATION

A row with no implementation is a connector that can never emit — and saying so
is far better than a `KeyError` at harvest time, or worse, a silent skip. The
`orphans()` check surfaces both directions of drift.
"""
from __future__ import annotations

from typing import Optional

import structlog

from .. import db
from . import base
from .sources import ALL, HttpSource

log = structlog.get_logger("connectors.registry")

_INSTANCES: dict[str, HttpSource] = {}


def _build() -> dict[str, HttpSource]:
    if not _INSTANCES:
        for cls in ALL:
            _INSTANCES[cls.name] = cls()
        # Service connectors (ollama, qdrant, anthropic) and search go through
        # the SAME probe/contract-test/dormancy machinery as sources. Having a
        # working credential is not the same as being `live`.
        from .services import ALL_SERVICES, duckduckgo
        for cls in ALL_SERVICES:
            _INSTANCES[cls.name] = cls()
        ddg = duckduckgo()
        _INSTANCES[ddg.name] = ddg
    return _INSTANCES


def get(name: str) -> HttpSource:
    try:
        return _build()[name]
    except KeyError:
        raise base.ConnectorError(
            f"no implementation for connector {name!r}; "
            f"implemented: {sorted(_build())}") from None


def implemented() -> list[str]:
    return sorted(_build())


def has(name: str) -> bool:
    return name in _build()


async def orphans() -> dict[str, list[str]]:
    """Drift between the registry table and the code, in both directions.

    `rows_without_code` is the dangerous one: a source enabled in the database
    that nothing can actually harvest. It would sit at zero yield forever and —
    before C3 — nothing would have noticed.
    """
    rows = {r["name"] for r in await db.fetch("SELECT name FROM sources WHERE enabled")}
    code = set(_build())
    return {
        "rows_without_code": sorted(rows - code),
        "code_without_rows": sorted(code - rows),
    }


async def register_all() -> int:
    """Ensure every implemented connector has a `connector_health` row.

    Everything starts DORMANT and must earn `live` by passing a contract test.
    A connector with no health row is treated as dormant anyway (absent is the
    safe direction), but an explicit row means it appears in `jpd connectors`
    rather than being invisible.
    """
    n = 0
    for name, impl in _build().items():
        # Service connectors (ollama, qdrant, anthropic, duckduckgo) have no
        # source_type — they are not discovery sources. Default rather than
        # requiring every connector to pretend it is one.
        kind = getattr(impl, "kind", "api")
        stype = getattr(impl, "source_type", None)
        await base.register(name, kind,
                            f"source_type={stype}" if stype else "service connector")
        n += 1
    return n
