"""Step result types.

🔴 THE ONE RULE THIS FILE EXISTS TO ENFORCE — read before "simplifying" it.

``StepStatus`` has no ``None``, no ``unknown``, and no default. Pimlico
persisted ``status=None`` for seven consecutive days across every stage
transition and nothing anywhere noticed, because a nullable status column
makes "we don't know what happened" indistinguishable from "nothing happened".

The DB column is ``NOT NULL`` with a ``CHECK``. This enum is the other half of
that constraint. If you add a member here you must ALTER the constraint in a
new migration, and vice versa.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class StepStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED_ON_HUMAN = "blocked_on_human"
    SKIPPED_DORMANT = "skipped_dormant"
    QUARANTINED = "quarantined"

    @property
    def terminal(self) -> bool:
        return self is not StepStatus.RUNNING


# Statuses the engine may set only after acceptance has been evaluated.
ACCEPTANCE_GATED = frozenset({StepStatus.SUCCEEDED})


@dataclass
class Evidence:
    """A piece of captured proof. Content-addressed, same discipline as
    artifacts and the source manifest — one mechanism, three uses."""
    url: Optional[str] = None
    sha256: str = ""
    fetched_at: Optional[str] = None
    http_status: Optional[int] = None
    mime: Optional[str] = None
    snippet: Optional[str] = None
    source_kind: str = "primary"          # primary | paraphrase | derived
    live_at_capture: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url, "sha256": self.sha256, "fetched_at": self.fetched_at,
            "http_status": self.http_status, "mime": self.mime,
            "snippet": self.snippet, "source_kind": self.source_kind,
            "live_at_capture": self.live_at_capture,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Evidence":
        return cls(**{k: d.get(k) for k in
                      ("url", "sha256", "fetched_at", "http_status", "mime",
                       "snippet", "source_kind")},
                   live_at_capture=bool(d.get("live_at_capture", False)))


@dataclass
class StepResult:
    """What a step function returns.

    The status a step *proposes* is not necessarily the status it *gets*. The
    engine runs the acceptance predicate and downgrades SUCCEEDED to FAILED if
    the predicate is false. A step cannot declare itself successful.
    """
    status: StepStatus
    data: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    cost_usd: float = 0.0
    reason: Optional[str] = None
    human_task_ref: Optional[str] = None

    def __post_init__(self) -> None:
        # Defensive: a str slipping in here would serialise fine and then fail
        # the DB CHECK at write time, far from the cause.
        if not isinstance(self.status, StepStatus):
            self.status = StepStatus(self.status)

    # -- convenience constructors, so call sites read as intent -------------
    @classmethod
    def ok(cls, **data: Any) -> "StepResult":
        return cls(status=StepStatus.SUCCEEDED, data=data)

    @classmethod
    def fail(cls, reason: str, **data: Any) -> "StepResult":
        return cls(status=StepStatus.FAILED, reason=reason, data=data)

    @classmethod
    def dormant(cls, connector: str) -> "StepResult":
        return cls(status=StepStatus.SKIPPED_DORMANT,
                   reason=f"connector not live: {connector}")

    @classmethod
    def blocked(cls, ref: str, reason: str) -> "StepResult":
        return cls(status=StepStatus.BLOCKED_ON_HUMAN,
                   reason=reason, human_task_ref=ref)

    @classmethod
    def quarantine(cls, reason: str, **data: Any) -> "StepResult":
        return cls(status=StepStatus.QUARANTINED, reason=reason, data=data)

    def to_json(self) -> str:
        return json.dumps({
            "status": self.status.value, "data": self.data,
            "cost_usd": self.cost_usd, "reason": self.reason,
            "human_task_ref": self.human_task_ref,
        }, default=str)

    def evidence_json(self) -> str:
        return json.dumps([e.to_dict() for e in self.evidence], default=str)

    @classmethod
    def rehydrate(cls, result_json: Any, evidence_json: Any) -> "StepResult":
        """Rebuild a StepResult from what was persisted.

        This is what makes ``jpd verify --last`` possible: the acceptance
        predicate is re-evaluated against the stored result, not against a
        summary of it. If a predicate cannot be re-run from persisted state,
        that predicate is untestable on resume and the step is lying about
        being verifiable.
        """
        r = json.loads(result_json) if isinstance(result_json, str) else (result_json or {})
        e = json.loads(evidence_json) if isinstance(evidence_json, str) else (evidence_json or [])
        return cls(
            status=StepStatus(r.get("status", "failed")),
            data=r.get("data") or {},
            evidence=[Evidence.from_dict(x) for x in e],
            cost_usd=float(r.get("cost_usd") or 0),
            reason=r.get("reason"),
            human_task_ref=r.get("human_task_ref"),
        )


class Phase(str, Enum):
    DISCOVER = "DISCOVER"
    RESEARCH = "RESEARCH"
    ROADMAP = "ROADMAP"
    OUTLINE = "OUTLINE"
    FORGE = "FORGE"
    MARKET = "MARKET"
    DELIVER = "DELIVER"


class Tier(str, Enum):
    ROADMAP = "roadmap"
    INSTRUCTIONS = "instructions"
    DEPLOYED = "deployed"
