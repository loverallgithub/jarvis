"""Typed artifacts returned by source connectors.

🔴 `call()` returns one of these, or it raises. It never returns a string.

Pimlico's entire LinkedIn incident was possible because
`_route_sintra_output(output_type, text: str, ...)` accepted a `str`, so
`f"[Automation failed: {e}]"` was indistinguishable from real output at every
layer downstream. A typed artifact makes that particular mistake unrepresentable:
there is no way to describe a failure in the shape of a `Signal`.

`Author` exists here — not in phase 4 — because the author is already in every
payload we parse. Pimlico parsed it and threw it away. Capturing it now costs
nothing and is what turns discovery from a topic generator into a launch
audience generator (DEC-004).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass(frozen=True)
class Author:
    """Who said it. Minimum needed to attribute a quote, and no more.

    ⚠️ `do_not_contact` defaults TRUE for anything from a community platform,
    enforced at the database level too. These are evidence, never a mailing
    list. Enrichment applies to `company` voices only — never to private
    individuals.
    """
    handle: str
    platform: str
    kind: str = "person"                    # person | company
    display_name: Optional[str] = None
    profile_url: Optional[str] = None
    org_name: Optional[str] = None
    org_domain: Optional[str] = None

    def __post_init__(self) -> None:
        if self.kind not in ("person", "company"):
            raise ValueError(f"author kind must be person|company, got {self.kind!r}")

    @property
    def community_sourced(self) -> bool:
        return self.platform in ("hackernews", "reddit", "stackoverflow",
                                 "github", "discourse", "appstore", "indiehackers")


@dataclass(frozen=True)
class Signal:
    """One observation from one source. The unit the funnel consumes."""
    external_id: str
    concept: str
    source_type: str
    url: Optional[str] = None
    body: Optional[str] = None
    observed_at: Optional[datetime] = None
    author: Optional[Author] = None
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # A signal with no id cannot be deduplicated, and a signal with no
        # concept cannot be clustered. Both are silent poison downstream, so
        # they are refused at construction.
        if not str(self.external_id or "").strip():
            raise ValueError("signal has no external_id — it cannot be deduplicated")
        if not str(self.concept or "").strip():
            raise ValueError("signal has no concept — it cannot be clustered")
        if self.source_type not in ("search", "launch", "community", "review",
                                    "filing", "authority"):
            raise ValueError(f"unknown source_type {self.source_type!r}")

    @property
    def admissible(self) -> bool:
        """The 4-content-word admission rule (03-PIPELINE A2).

        Bare brand names and one-word review titles embed as mutually similar.
        In Pimlico they formed a 20-member false cluster that would have cleared
        every gate and auto-built garbage. Cheap to check, expensive to miss.
        """
        words = [w for w in self.concept.split() if len(w) > 2]
        return len(words) >= 4

    def digest(self) -> str:
        return hashlib.sha256(
            f"{self.source_type}|{self.external_id}|{self.concept}".encode()).hexdigest()

    def at(self) -> datetime:
        return self.observed_at or datetime.now(timezone.utc)


@dataclass(frozen=True)
class HarvestResult:
    """What a harvest produced, including the fact that it produced nothing.

    🔴 `count == 0` is a FAILURE SIGNAL, not a quiet success. Pimlico's
    google_trends, indie_hackers and app_store_reviews returned 0 items every
    day with `dormant: []`, because `dormant` was a hand-set flag nobody set.
    This type makes the zero explicit so the state machine can act on it.
    """
    connector: str
    signals: list[Signal] = field(default_factory=list)
    detail: str = ""

    @property
    def count(self) -> int:
        return len(self.signals)

    @property
    def admissible(self) -> list[Signal]:
        return [s for s in self.signals if s.admissible]
