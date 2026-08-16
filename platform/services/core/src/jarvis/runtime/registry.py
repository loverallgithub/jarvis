"""The ``@step`` decorator and the step registry.

Two registration-time refusals live here, and both are deliberate:

1. **No acceptance predicate → no registration.** A step that cannot state
   what "worked" means cannot report that it worked.
2. **No existing test file → no registration.** (C2) Pimlico shipped eleven
   alert rules and four of its detectors are silently broken today, because a
   detector nobody tested is not a detector. The same reasoning applies to a
   pipeline step. ``test=`` must name a path that exists on disk *now*, at
   import time — not a path someone intends to create.

Both raise at import, so a bad step takes the container down at startup rather
than failing quietly on the one day it runs.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from ..config import settings
from .types import StepResult


class StepDefinitionError(RuntimeError):
    """A step is declared in a way the engine refuses to accept."""


AcceptancePredicate = Callable[[StepResult], bool]


@dataclass(frozen=True)
class StepSpec:
    id: str
    phase: str
    fn: Callable[..., Any]
    inputs: tuple[str, ...]
    produces: tuple[str, ...]
    requires_connectors: tuple[str, ...]
    # ANY-of, not all-of: "at least one of these routes is live". A step with a
    # genuine fallback (two LLM providers) is not satisfied by AND, and must
    # not be relaxed to "no requirement" either — the gate is what stops a
    # broken dependency being carried past.
    requires_any_connectors: tuple[str, ...]
    acceptance: AcceptancePredicate
    acceptance_desc: str
    idempotency_key: Optional[str]
    timeout_s: int
    cost_budget_usd: float
    test: str
    repairable: bool
    max_repairs: int
    description: str = ""

    def evaluate_acceptance(self, result: StepResult) -> tuple[bool, str]:
        """Run the predicate, converting any exception into a hard False.

        A predicate that raises is NOT a pass. Pimlico's verification code
        swallowed exceptions into truthy defaults more than once; here the
        exception text becomes the recorded reason.
        """
        try:
            ok = bool(self.acceptance(result))
        except Exception as e:                                  # noqa: BLE001
            return False, f"acceptance predicate raised {type(e).__name__}: {e}"
        return ok, ("accepted" if ok else f"acceptance failed: {self.acceptance_desc}")


_REGISTRY: dict[str, StepSpec] = {}


def _resolve_test_path(test: str) -> Path:
    p = Path(test)
    return p if p.is_absolute() else Path(settings.package_root) / test


def step(
    *,
    id: str,
    phase: str,
    acceptance: AcceptancePredicate,
    test: str,
    inputs: Sequence[str] = (),
    produces: Sequence[str] = (),
    requires_connectors: Sequence[str] = (),
    requires_any_connectors: Sequence[str] = (),
    acceptance_desc: str = "",
    idempotency_key: Optional[str] = None,
    timeout_s: Optional[int] = None,
    cost_budget_usd: float = 0.0,
    repairable: bool = False,
    max_repairs: Optional[int] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        if id in _REGISTRY:
            raise StepDefinitionError(f"duplicate step id {id!r}")
        if not callable(acceptance):
            raise StepDefinitionError(f"{id}: acceptance must be callable")
        if not inspect.iscoroutinefunction(fn):
            raise StepDefinitionError(
                f"{id}: step functions must be async — a blocking step holds the "
                f"event loop and outlives its lease (Pimlico's clustering took "
                f"181s inline and froze HTTP)")

        tp = _resolve_test_path(test)
        if not tp.exists():
            raise StepDefinitionError(
                f"{id}: declared test {test!r} does not exist at {tp}. "
                f"A step cannot be registered without a test. Write the test "
                f"in the same commit as the step (C2).")

        spec = StepSpec(
            id=id, phase=phase, fn=fn,
            inputs=tuple(inputs), produces=tuple(produces),
            requires_connectors=tuple(requires_connectors),
            requires_any_connectors=tuple(requires_any_connectors),
            acceptance=acceptance,
            acceptance_desc=acceptance_desc or _describe(acceptance),
            idempotency_key=idempotency_key,
            timeout_s=timeout_s or settings.default_timeout_s,
            cost_budget_usd=cost_budget_usd,
            test=test,
            repairable=repairable,
            max_repairs=max_repairs if max_repairs is not None else settings.repair_ceiling,
            description=(fn.__doc__ or "").strip().split("\n")[0],
        )
        _REGISTRY[id] = spec
        fn.__step_spec__ = spec          # type: ignore[attr-defined]
        return fn

    return decorate


def _describe(pred: AcceptancePredicate) -> str:
    """Best-effort source text of a predicate, for the failure message.

    Worth the effort: 'acceptance failed' with no detail is the kind of log
    line that costs an hour. With the source inline, the reason column tells
    you what was expected without opening the file.
    """
    try:
        src = inspect.getsource(pred).strip()
        return " ".join(src.split())[:400]
    except Exception:                                          # noqa: BLE001
        return getattr(pred, "__name__", "predicate")


def get(step_id: str) -> StepSpec:
    try:
        return _REGISTRY[step_id]
    except KeyError:
        raise StepDefinitionError(f"unknown step {step_id!r}") from None


def all_steps() -> dict[str, StepSpec]:
    return dict(_REGISTRY)


def validate_registry() -> list[str]:
    """Re-check every registered step at startup. Returns problems found.

    Registration already refuses bad steps, so this should always be empty.
    It runs anyway, because "should always be empty" is exactly the assumption
    that was wrong every previous time on this host.
    """
    problems: list[str] = []
    for sid, spec in _REGISTRY.items():
        tp = _resolve_test_path(spec.test)
        if not tp.exists():
            problems.append(f"{sid}: test {spec.test} vanished from {tp}")
        if spec.timeout_s <= 0:
            problems.append(f"{sid}: non-positive timeout {spec.timeout_s}")
        if spec.repairable and spec.max_repairs <= 0:
            problems.append(f"{sid}: repairable with max_repairs={spec.max_repairs}")
    return problems


def _reset_for_tests() -> None:
    _REGISTRY.clear()
