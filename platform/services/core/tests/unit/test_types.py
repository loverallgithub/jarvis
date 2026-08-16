"""Status-type invariants.

The headline defect these protect against: Pimlico persisted `status=None`
for seven consecutive days across every stage transition, and nothing noticed,
because a nullable status makes "we don't know" indistinguishable from
"nothing happened".
"""
from __future__ import annotations

import pytest

from jarvis.runtime.types import Evidence, StepResult, StepStatus


def test_there_is_no_null_or_unknown_status():
    values = {s.value for s in StepStatus}
    assert values == {"running", "succeeded", "failed", "blocked_on_human",
                      "skipped_dormant", "quarantined"}
    for forbidden in (None, "", "unknown", "none", "null"):
        with pytest.raises(ValueError):
            StepStatus(forbidden)


def test_status_cannot_be_defaulted():
    """StepResult has no default status — the caller must state one."""
    with pytest.raises(TypeError):
        StepResult()                                    # type: ignore[call-arg]


def test_a_string_status_is_coerced_or_rejected_at_construction():
    """A str slipping through would serialise fine and then fail the DB CHECK
    far from the cause. Catch it at the boundary."""
    assert StepResult(status="succeeded").status is StepStatus.SUCCEEDED  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        StepResult(status="probably_fine")              # type: ignore[arg-type]


def test_round_trip_preserves_everything_the_predicate_needs():
    """`jpd verify --last` re-runs the predicate against PERSISTED state.

    If a round trip loses a field, the resume rule silently degrades into
    "trust the recorded status" — which is exactly what it exists to replace.
    """
    original = StepResult(
        status=StepStatus.SUCCEEDED,
        data={"need_id": 7, "sections": ["a", "b"]},
        evidence=[Evidence(url="https://example.test/a", sha256="deadbeef",
                           http_status=200, live_at_capture=True,
                           source_kind="primary", snippet="hello")],
        cost_usd=0.25, reason=None)

    back = StepResult.rehydrate(original.to_json(), original.evidence_json())

    assert back.status is StepStatus.SUCCEEDED
    assert back.data == original.data
    assert back.cost_usd == 0.25
    assert len(back.evidence) == 1
    e = back.evidence[0]
    assert (e.url, e.sha256, e.http_status, e.live_at_capture, e.source_kind) == \
           ("https://example.test/a", "deadbeef", 200, True, "primary")


def test_paraphrase_survives_the_round_trip():
    """DEC-003 / TubeOnAI. `source_kind='paraphrase'` is what the publish
    predicate rejects — if it were lost in serialisation, a summary could back
    a published claim."""
    r = StepResult(status=StepStatus.SUCCEEDED,
                   evidence=[Evidence(sha256="x", source_kind="paraphrase")])
    back = StepResult.rehydrate(r.to_json(), r.evidence_json())
    assert back.evidence[0].source_kind == "paraphrase"


def test_terminal_property():
    assert StepStatus.RUNNING.terminal is False
    for s in (StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.BLOCKED_ON_HUMAN,
              StepStatus.SKIPPED_DORMANT, StepStatus.QUARANTINED):
        assert s.terminal is True


def test_constructors_read_as_intent():
    assert StepResult.dormant("sintra").status is StepStatus.SKIPPED_DORMANT
    assert "sintra" in StepResult.dormant("sintra").reason
    assert StepResult.blocked("JPD-1", "waiting").status is StepStatus.BLOCKED_ON_HUMAN
    assert StepResult.quarantine("malformed").status is StepStatus.QUARANTINED
    assert StepResult.fail("nope").status is StepStatus.FAILED
