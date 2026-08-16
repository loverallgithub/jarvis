"""A4 — the gates, and the census that makes them tunable.

────────────────────────────────────────────────────────────────────────────
C6: EVERY EVALUATION IS PERSISTED — PASS *AND* FAIL
────────────────────────────────────────────────────────────────────────────
Pimlico's near-miss census lived in per-process memory and was lost on every
restart. Three weeks of accumulated discovery produced **zero promotions** and
there was no way to diagnose it: nobody could say which gate blocked what, or
by how much. That is the single most expensive omission in the whole platform.

Here every gate evaluation is a row. Which makes the question that matters —
*"what would have promoted at severity ≥ 3.5?"* — a SQL query rather than a
rebuild, and turns gate tuning from guesswork into arithmetic over real data.

────────────────────────────────────────────────────────────────────────────
TWO RULES THAT ARE EASY TO GET WRONG
────────────────────────────────────────────────────────────────────────────
**1. `authority` cannot self-corroborate.** All creator channels share one
source_type precisely so a need supported only by influencer opinion cannot
clear the cross-source gate. Without this, the system could build a product
because one person said something compelling.

**2. Frequency counts DISTINCT VOICES, not rows.** Five mentions from one loud
person is not a market. This is only possible because A1d captured the author.

────────────────────────────────────────────────────────────────────────────
🔴 3. A DISABLED GATE IS NOT A PASSED GATE
────────────────────────────────────────────────────────────────────────────
`thresholds()` reads `WHERE enabled`, and `add()` used to skip any gate absent
from that dict — silently. Since `all([])` is **True**, a `Verdict` built from
zero gates *passed*, and the docstring "ALL gates must pass" was satisfied by
all zero of them. Proven 2026-08-08 against the test database: one cluster that
failed 6/6 gates promoted cleanly once the rows were disabled.

That mattered because **retuning gates is a DATA operation by design** — the
same rule as source config and price ratios. The supported way to tune a gate
was also the way to delete it, and the partial case was worse than the total
one: drop `cross_source` alone and rule 1 above stops being enforced while
`failed_gates` stays empty and the log still reads `passed=True`.

So `REQUIRED_GATES` must all be present, `evaluate()` raises `GateConfigError`
if any is missing, and an empty verdict can never pass. Threshold VALUES stay
freely tunable — that is the knob this was always meant to expose.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import structlog

from .. import db
from .cluster import Cluster

log = structlog.get_logger("discovery.gates")

# Every gate that must be evaluated for a verdict to mean anything. Tuning a
# threshold's VALUE is data; removing a gate from the decision is not.
REQUIRED_GATES = ("frequency", "severity", "distinct_voices",
                  "commercial_intent", "recency_days", "cross_source")


class GateConfigError(RuntimeError):
    """The gate configuration cannot produce a trustworthy verdict.

    Raised rather than returned, and never downgraded to a failing verdict: a
    misconfigured gate set is an OPERATOR problem, and reporting it as "this
    cluster did not qualify" would hide it behind a plausible-looking result.
    """

# Words that indicate somebody is spending, buying, hiring or tooling up.
# Pain without budget is not a product.
COMMERCIAL_MARKERS = (
    "pay", "paid", "paying", "price", "pricing", "cost", "costs", "budget",
    "spend", "spending", "subscription", "license", "licence", "invoice",
    "vendor", "contract", "hiring", "hire", "consultant", "agency", "saas",
    "per seat", "per month", "annual", "procurement", "revenue", "billing",
)

# Words that indicate genuine pain rather than a product announcement. Severity
# is averaged over PAIN evidence only — launches do not count.
# Widened after reading real signals: the first draft missed "suck",
# "hopeless", "always breaking", "needs improvement" — which is how people
# actually complain. A lexicon written from imagination scored genuine
# 1-star reviews at 0.0.
PAIN_MARKERS = {
    3.0: ("annoying", "clunky", "slow", "confusing", "tedious", "manual",
          "needs improvement", "not great", "hard to", "difficult", "buggy",
          "inaccurate", "innacurate", "clumsy", "awkward"),
    4.0: ("broken", "wasting", "waste", "nightmare", "impossible", "terrible",
          "unusable", "losing", "lost", "fails", "failing", "hours every",
          "suck", "sucks", "hopeless", "always breaking", "doesn't work",
          "does not work", "don't work", "never works", "useless", "awful",
          "worst", "unreliable", "keeps crashing", "freezing", "over and over"),
    5.0: ("costing us", "lose money", "lost revenue", "six figures", "fortune",
          "cannot", "no way to", "gave up", "had to hire", "had to switch",
          "switched away", "cancelled", "unusable for work"),
}


@dataclass
class GateResult:
    gate: str
    value: float
    threshold: float
    comparator: str
    passed: bool

    @property
    def margin(self) -> float:
        """How far past (or short of) the line. A near-miss is the most
        interesting row in the census."""
        return float(self.value) - float(self.threshold)


@dataclass
class Verdict:
    cluster_id: Optional[int]
    results: list[GateResult]

    @property
    def passed(self) -> bool:
        """ALL gates must pass — and there must BE gates.

        🔴 `all([])` is True. Without the emptiness check, a verdict carrying
        no gate results reports `passed` and promotes whatever it was asked
        about. A gate that can be outvoted is not a gate; a gate set that can
        be empty is not a gate set.
        """
        if not self.results:
            return False
        return all(r.passed for r in self.results)

    @property
    def failed_gates(self) -> list[str]:
        return [r.gate for r in self.results if not r.passed]

    def get(self, gate: str) -> Optional[GateResult]:
        return next((r for r in self.results if r.gate == gate), None)


async def thresholds() -> dict[str, tuple[float, str]]:
    rows = await db.fetch(
        "SELECT gate, threshold, comparator FROM gate_thresholds WHERE enabled")
    return {r["gate"]: (float(r["threshold"]), r["comparator"]) for r in rows}


def _compare(value: float, threshold: float, comparator: str) -> bool:
    return {
        ">=": value >= threshold, "<=": value <= threshold,
        ">": value > threshold, "<": value < threshold,
        "=": value == threshold,
    }[comparator]


def independent_mentions(cluster: Cluster) -> int:
    """How many INDEPENDENT sources of this signal there are.

    🔴 The gate is about independence, not about people. Written as a raw voice
    count it was **unsatisfiable for authorless sources**: Google Suggest has no
    author by construction — an autocomplete phrase is a demand signal, not a
    person — so 11 real search signals could never clear `distinct_voices >= 3`
    no matter how strong they were.

    A signal with no author is independent BY CONSTRUCTION (it came from a
    different query), so it counts as one. A signal with an author counts once
    per DISTINCT author, which preserves the property that actually matters:
    five mentions from one loud person is still one voice.
    """
    voiced = {d.voice_id for d in cluster.members if d.voice_id is not None}
    authorless = sum(1 for d in cluster.members if d.voice_id is None)
    return len(voiced) + authorless


def severity_of(cluster: Cluster) -> float:
    """Average pain intensity across the cluster's PAIN evidence.

    A cluster with no pain markers at all scores 0 rather than a neutral
    default: "we found no evidence of pain" must not look like "moderate pain".
    """
    scores: list[float] = []
    for m in cluster.members:
        text = (m.concept or "").lower()
        best = 0.0
        for weight, markers in PAIN_MARKERS.items():
            if any(mk in text for mk in markers):
                best = max(best, weight)
        if best:
            scores.append(best)
    return round(sum(scores) / len(scores), 2) if scores else 0.0


def commercial_intent_of(cluster: Cluster) -> int:
    return sum(1 for m in cluster.members
               if any(mk in (m.concept or "").lower() for mk in COMMERCIAL_MARKERS))


def cross_source_of(cluster: Cluster) -> int:
    """Distinct source types — with authority unable to corroborate itself.

    All creator channels share `authority`, so they collapse to one type here
    no matter how many of them agree.
    """
    return len(cluster.source_types)


def authority_only(cluster: Cluster) -> bool:
    return cluster.source_types == {"authority"}


async def evaluate(cluster: Cluster, cluster_id: Optional[int] = None,
                   run_id: Optional[int] = None,
                   recency_days: Optional[float] = None) -> Verdict:
    """Run every gate and PERSIST every result, pass or fail."""
    th = await thresholds()
    results: list[GateResult] = []
    missing: list[str] = []

    def add(gate: str, value: float) -> None:
        if gate not in th:
            # Recorded, never skipped quietly — see rule 3 in the module docstring.
            missing.append(gate)
            return
        limit, comparator = th[gate]
        results.append(GateResult(gate, float(value), limit, comparator,
                                  _compare(float(value), limit, comparator)))

    add("frequency", independent_mentions(cluster))
    add("severity", severity_of(cluster))
    add("distinct_voices", independent_mentions(cluster))
    add("commercial_intent", commercial_intent_of(cluster))
    add("recency_days", recency_days if recency_days is not None else 0.0)

    # cross_source, with the authority rule applied explicitly rather than
    # left as an emergent property nobody can see.
    xs = cross_source_of(cluster)
    if authority_only(cluster):
        xs = 1
        log.info("gates.authority_cannot_self_corroborate", cluster_id=cluster_id)
    add("cross_source", xs)

    # 🔴 Before the verdict exists, not after. A missing gate must never reach
    # `Verdict.passed`, because at that point the only remaining signal is an
    # empty list that reads exactly like "everything passed".
    absent = [g for g in REQUIRED_GATES if g in missing or g not in th]
    if absent:
        log.error("gates.config_invalid", cluster_id=cluster_id, missing=absent,
                  evaluated=[r.gate for r in results])
        raise GateConfigError(
            f"required gates absent from `gate_thresholds WHERE enabled`: "
            f"{absent}. A disabled gate is not a passed gate — re-enable the "
            f"row, or tune its `threshold` value instead of removing it.")

    verdict = Verdict(cluster_id=cluster_id, results=results)

    for r in results:
        await db.execute(
            """
            INSERT INTO gate_evaluations (run_id, cluster_id, gate, value, threshold, passed)
            VALUES ($1,$2,$3,$4,$5,$6)
            """,
            run_id, cluster_id, r.gate, r.value, r.threshold, r.passed)

    log.info("gates.evaluated", cluster_id=cluster_id, passed=verdict.passed,
             failed=verdict.failed_gates,
             values={r.gate: r.value for r in results})
    return verdict


# ---------------------------------------------------------------------------
# counterfactual replay — the whole reason the census is persisted
# ---------------------------------------------------------------------------

async def census(limit: int = 200) -> list[dict]:
    """Per-gate pass rates and near-miss margins over everything evaluated."""
    rows = await db.fetch(
        """
        SELECT gate,
               count(*)                                   AS evaluations,
               count(*) FILTER (WHERE passed)             AS passes,
               round(avg(value)::numeric, 2)              AS avg_value,
               round(min(threshold)::numeric, 2)          AS threshold,
               round(avg(value - threshold)::numeric, 2)  AS avg_margin,
               round(max(value)::numeric, 2)              AS best_value
          FROM gate_evaluations
         GROUP BY gate ORDER BY passes ASC, gate
        """)
    return [dict(r) for r in rows]


async def blocking_gate() -> list[dict]:
    """Which gate blocks the most clusters. The first question to ask when
    nothing promotes — and the one Pimlico could never answer."""
    rows = await db.fetch(
        """
        SELECT gate, count(*) AS blocked
          FROM gate_evaluations
         WHERE passed = FALSE
           AND cluster_id IS NOT NULL
         GROUP BY gate ORDER BY blocked DESC
        """)
    return [dict(r) for r in rows]


async def replay(overrides: dict[str, float]) -> dict:
    """*"What would have promoted at severity ≥ 3.5?"* — as arithmetic.

    Replays the stored census against hypothetical thresholds. No re-harvest,
    no re-cluster, no rebuild: this is the payoff for persisting every
    evaluation, and it turns gate tuning into a measurement.
    """
    th = await thresholds()
    effective = {g: (overrides.get(g, v[0]), v[1]) for g, v in th.items()}
    for g, v in overrides.items():
        effective.setdefault(g, (v, ">="))

    rows = await db.fetch(
        """
        SELECT cluster_id, gate, value FROM gate_evaluations
         WHERE cluster_id IS NOT NULL
           AND (cluster_id, gate, evaluated_at) IN (
                 SELECT cluster_id, gate, max(evaluated_at)
                   FROM gate_evaluations WHERE cluster_id IS NOT NULL
                  GROUP BY cluster_id, gate)
        """)

    by_cluster: dict[int, dict[str, float]] = {}
    for r in rows:
        by_cluster.setdefault(int(r["cluster_id"]), {})[r["gate"]] = float(r["value"])

    would_pass, blocked_by = [], {}
    for cid, values in by_cluster.items():
        failed = [g for g, (lim, cmp_) in effective.items()
                  if g in values and not _compare(values[g], lim, cmp_)]
        if failed:
            for g in failed:
                blocked_by[g] = blocked_by.get(g, 0) + 1
        else:
            would_pass.append(cid)

    return {
        "overrides": overrides,
        "clusters_evaluated": len(by_cluster),
        "would_promote": sorted(would_pass),
        "would_promote_count": len(would_pass),
        "still_blocked_by": dict(sorted(blocked_by.items(), key=lambda kv: -kv[1])),
    }
