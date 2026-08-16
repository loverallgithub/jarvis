"""The section plan for each tier.

────────────────────────────────────────────────────────────────────────────
THE THREE TIERS ARE ONE DOCUMENT, GROWN THREE TIMES
────────────────────────────────────────────────────────────────────────────
Roadmap ⊂ Instructions ⊂ Deployed. Each tier is a strict superset of the one
below, which is what makes the upgrade path deliver only the delta and what
makes a failed Deployed build still leave two sellable products behind.

Pimlico's all-or-nothing model turned every build failure into zero revenue.

────────────────────────────────────────────────────────────────────────────
🔴 THE SIZE CAP TRUNCATES THE PLAN
────────────────────────────────────────────────────────────────────────────
`sections_for(tier, max_sections=N)` returns a SHORTER PLAN, not a full plan
with a note that some sections are missing. Pimlico capped the generated output
while keeping the full plan, so `verify` then failed on "fewer sections than
planned" — the run was **guaranteed to fail after paying for every section it
had already generated**. The cap and the contract must be the same object.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    key: str
    heading: str
    brief: str
    min_words: int = 120


# Phase C — the Roadmap. "Here is exactly what to build, in what order, with
# what tools, at what cost, and why."
ROADMAP: tuple[Section, ...] = (
    Section("outcome", "The Outcome",
            "One measurable result, one metric, one timeframe. State how the "
            "buyer will know they got it. Cite the evidence for why this "
            "outcome matters."),
    Section("audience", "Who This Is For",
            "The specific operator or business this addresses, drawn from the "
            "named voices in the research. Be concrete about size and context."),
    Section("milestones", "Milestones & Critical Path",
            "Numbered milestones. EVERY milestone needs an owner, a duration, "
            "and either a dependency or an explicit 'no dependencies' marker. "
            "No orphan milestones."),
    Section("stack", "Stack Selection",
            "Each tool named with what it costs and why it was chosen. Flag "
            "anything with no API as requiring manual operation, honestly, "
            "up front."),
    Section("estimate", "Effort, Cost & Confidence",
            "Effort and cost with a confidence interval, anchored on the "
            "observed pricing evidence — not invented."),
    Section("risks", "Risk Register",
            "What kills this, how likely, and what to do instead. Each risk "
            "cited to the research where possible."),
)

# Phase D — the Instructions. "A competent operator can execute without asking
# a single question."
INSTRUCTIONS_EXTRA: tuple[Section, ...] = (
    Section("decomposition", "Build Steps",
            "Milestones broken into concrete, ordered build steps. Each step "
            "states WHAT, WHY, HOW and WHERE — exact clicks, exact field names, "
            "exact commands.", 200),
    Section("configuration", "Configuration & Credentials",
            "Every credential, env var, DNS record, webhook and permission the "
            "buyer needs, with where to obtain each one. Document the traps.", 180),
    Section("verification", "How To Verify Each Step",
            "For every build step, a check the buyer can run to prove it "
            "worked, with the expected output.", 150),
)

# Phase E — the Deployed system.
DEPLOYED_EXTRA: tuple[Section, ...] = (
    Section("architecture", "As-Built Architecture",
            "The concrete architecture of the delivered system: components, "
            "data flow, and where each piece runs.", 180),
    Section("runbook", "Operations Runbook",
            "How to run it day to day: what to monitor, what breaks, what to "
            "do when it does.", 180),
    Section("handover", "Handover & Acceptance",
            "What is being handed over, the acceptance tests that prove it "
            "works, and what ongoing ownership looks like.", 150),
)

TIER_ORDER = ("roadmap", "instructions", "deployed")


def sections_for(tier: str, max_sections: int | None = None) -> list[Section]:
    """The plan for a tier — already truncated if a cap applies.

    Callers must treat the returned list as the CONTRACT. Verification checks
    against exactly this, so a cap can never create a shortfall.
    """
    if tier not in TIER_ORDER:
        raise ValueError(f"unknown tier {tier!r}; expected one of {TIER_ORDER}")

    plan = list(ROADMAP)
    if tier in ("instructions", "deployed"):
        plan += list(INSTRUCTIONS_EXTRA)
    if tier == "deployed":
        plan += list(DEPLOYED_EXTRA)

    if max_sections is not None and max_sections > 0:
        plan = plan[:max_sections]
    return plan


def tiers_up_to(tier: str) -> list[str]:
    return list(TIER_ORDER[: TIER_ORDER.index(tier) + 1])
