"""A5–A7 — qualify, score, promote.

────────────────────────────────────────────────────────────────────────────
`gap` STAYS NULL, AND THAT IS THE POINT
────────────────────────────────────────────────────────────────────────────
Pimlico weighted `gap` — "how underserved is this?" — at **0.25**, its second
highest weight, **with no competitive data at all**. A quarter of every score
was a number invented from nothing.

Here `gap` is not scored in Phase A. It is deferred to Phase B where competitive
evidence actually exists, and the need carries `gap: NULL` until then. A score
that omits a component honestly beats one that fabricates it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

import structlog

from .. import db
from .cluster import Cluster
from .gates import Verdict, commercial_intent_of, severity_of

log = structlog.get_logger("discovery.funnel")

# Above this, promotion is automatic; below it the operator decides (A7).
AUTO_PROMOTE_SCORE = 7.0


@dataclass
class Qualification:
    company_voices: list[dict] = field(default_factory=list)
    person_voices: int = 0
    named_orgs: list[str] = field(default_factory=list)
    qualified: bool = False
    reason: str = ""

    def to_json(self) -> str:
        return json.dumps({
            "company_voices": self.company_voices, "person_voices": self.person_voices,
            "named_orgs": self.named_orgs, "qualified": self.qualified,
            "reason": self.reason}, default=str)


async def qualify(cluster_id: int) -> Qualification:
    """A5 — who holds this pain, and can they pay?

    Reads the VOICES attached to the cluster rather than inferring an audience.
    Qualification becomes a list of named organisations instead of a guess —
    and that exists only because A1d captured the author.

    ⚠️ Enrichment (Databar) applies to `company` voices ONLY. Never to private
    individuals. Not implemented yet: databar is dormant, so this returns what
    the voices themselves already say.
    """
    rows = await db.fetch(
        """
        SELECT DISTINCT v.id, v.kind, v.display_name, v.org_name, v.org_domain,
               v.platform, v.do_not_contact
          FROM voices v
          JOIN voice_mentions vm ON vm.voice_id = v.id
          JOIN signals g ON g.id = vm.signal_id
         WHERE g.cluster_id = $1
        """, cluster_id)

    companies = [dict(r) for r in rows if r["kind"] == "company"]
    people = sum(1 for r in rows if r["kind"] == "person")
    named = sorted({(c["org_name"] or c["display_name"]) for c in companies
                    if c["org_name"] or c["display_name"]})

    # ≥3 named company voices, OR a quantified audience (people count as a
    # proxy for that until enrichment exists). A need that cannot be qualified
    # is PARKED, not built.
    if len(named) >= 3:
        return Qualification(companies, people, named, True,
                             f"{len(named)} named company voices")
    if people >= 3:
        return Qualification(companies, people, named, True,
                             f"{people} distinct people describing this pain")
    return Qualification(
        companies, people, named, False,
        f"only {len(named)} company voices and {people} people — cannot qualify; "
        f"the need is PARKED rather than built")


async def weights() -> dict[str, float]:
    rows = await db.fetch("SELECT component, weight FROM score_weights")
    return {r["component"]: float(r["weight"]) for r in rows}


@dataclass
class Score:
    total: float
    components: dict[str, float]
    gap: Optional[float] = None          # deliberately None until Phase B

    @property
    def auto_promotable(self) -> bool:
        return self.total >= AUTO_PROMOTE_SCORE


async def score(cluster: Cluster, verdict: Verdict) -> Score:
    """A6 — weighted sub-scores, normalised to /10.

    Each component is scaled against its own gate threshold, so "just passed"
    scores ~1x and "comfortably past" scores higher. Weights are DB rows.
    """
    w = await weights()
    comps: dict[str, float] = {}

    def norm(gate: str, cap: float = 2.0) -> float:
        r = verdict.get(gate)
        if r is None or not r.threshold:
            return 0.0
        return min(float(r.value) / float(r.threshold), cap) / cap

    comps["frequency"] = norm("frequency")
    comps["severity"] = norm("severity")
    comps["cross_source"] = norm("cross_source")
    comps["commercial_intent"] = norm("commercial_intent")

    total = sum(comps[k] * w.get(k, 0.0) for k in comps)
    denom = sum(w.get(k, 0.0) for k in comps) or 1.0
    return Score(total=round((total / denom) * 10, 2), components=comps, gap=None)


def _title_key(title: str) -> str:
    """Order-insensitive identity for a cluster label like 'a / b / c'."""
    return " / ".join(sorted(t.strip().lower() for t in (title or "").split("/") if t.strip()))


async def promote(cluster: Cluster, cluster_id: int, verdict: Verdict,
                  qualification: Qualification, sc: Score,
                  run_id: Optional[int] = None,
                  promoted_by: str = "auto") -> Optional[int]:
    """A7 — create the Need.

    Only reached when every gate passed AND qualification succeeded. Refusing
    here rather than promoting-then-filtering means `needs` never contains a
    row that should not have been created.
    """
    if not verdict.passed:
        log.info("funnel.not_promoted", cluster_id=cluster_id,
                 failed=verdict.failed_gates)
        return None
    if not qualification.qualified:
        # PARKED, not built — an explicit status, not a silent drop.
        log.info("funnel.parked", cluster_id=cluster_id, reason=qualification.reason)
        return None

    title = cluster.label[:200]

    # Dedup against needs that already exist. Clusters are re-created on every
    # run (cluster.persist is a plain INSERT), so without this every
    # `discover run` re-promoted the same pain as a brand-new need — by
    # 2026-08-16 six rows in `needs` were two actual needs, and an autonomous
    # scheduler would have paid for research and forge on each copy.
    # Identity is the label's token SET, not the label string: the same cluster
    # surfaced as "payabl / automat / account" one run and
    # "automat / payabl / account" the next.
    key = _title_key(title)
    for r in await db.fetch(
            "SELECT id, title FROM needs WHERE status NOT IN ('parked','rejected')"):
        if _title_key(r["title"]) == key:
            # Still attach this run's voices to the EXISTING need — the pain
            # re-surfacing is new launch audience, not a new need.
            await db.execute(
                """
                UPDATE voice_mentions SET need_id = $1
                 WHERE signal_id IN (SELECT id FROM signals WHERE cluster_id = $2)
                   AND need_id IS NULL
                """, int(r["id"]), cluster_id)
            log.info("funnel.duplicate_skipped", cluster_id=cluster_id,
                     existing_need=int(r["id"]), title=title)
            return None
    pain = max((m.concept for m in cluster.members), key=len)[:2000]
    audience = (", ".join(qualification.named_orgs[:5])
                or f"{qualification.person_voices} distinct voices")

    need_id = await db.fetchval(
        """
        INSERT INTO needs (cluster_id, title, pain_statement, audience, status, score,
                           gap, frequency, severity, cross_source, commercial_intent,
                           distinct_voices, qualification, promoted_by, run_id)
        VALUES ($1,$2,$3,$4,'promoted',$5,
                NULL, $6,$7,$8,$9,$10,$11::jsonb,$12,$13)
        RETURNING id
        """,
        cluster_id, title, pain, audience, sc.total,
        (verdict.get("frequency").value if verdict.get("frequency") else None),
        (verdict.get("severity").value if verdict.get("severity") else None),
        (int(verdict.get("cross_source").value) if verdict.get("cross_source") else None),
        (int(verdict.get("commercial_intent").value)
         if verdict.get("commercial_intent") else None),
        cluster.distinct_voices, qualification.to_json(), promoted_by, run_id)

    # Attach the voices to the need, so F5b has its launch audience.
    await db.execute(
        """
        UPDATE voice_mentions SET need_id = $1
         WHERE signal_id IN (SELECT id FROM signals WHERE cluster_id = $2)
        """, int(need_id), cluster_id)

    log.info("funnel.promoted", need_id=int(need_id), cluster_id=cluster_id,
             score=sc.total, source_types=sorted(cluster.source_types),
             distinct_voices=cluster.distinct_voices)
    return int(need_id)


async def announce(need_id: int, verdict: Verdict, cluster: Cluster) -> None:
    """Post the promotion to #discoveries WITH the gate census that let it
    through — so the decision is auditable, not just announced."""
    from ..console import cards
    from ..console.telegram import TelegramClient

    need = await db.fetchrow(
        "SELECT title, score, audience FROM needs WHERE id = $1", need_id)
    lines = [f"<b>{need['title']}</b>",
             f"score {need['score']}/10 · {cluster.size} signals · "
             f"{cluster.distinct_voices} distinct voices",
             f"sources: {', '.join(sorted(cluster.source_types))}",
             f"audience: {need['audience'][:120]}", "", "<b>gate census</b>"]
    for r in verdict.results:
        lines.append(f"  {r.gate}: {r.value} vs {r.threshold} "
                     f"({'pass' if r.passed else 'FAIL'})")
    lines += ["", "<b>top evidence</b>"]
    for m in sorted(cluster.members, key=lambda d: -len(d.concept))[:3]:
        lines.append(f"  • {m.concept[:150]}")

    try:
        await TelegramClient().post("discoveries", "\n".join(lines))
    except Exception as e:                                       # noqa: BLE001
        # Telegram being dormant must not undo a promotion.
        log.warning("funnel.announce_failed", need_id=need_id, error=str(e)[:150])
