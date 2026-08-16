"""A2–A3 — normalise and cluster.

────────────────────────────────────────────────────────────────────────────
WHY THIS IS LEXICAL, AND WHY THAT IS NOT A COMPROMISE
────────────────────────────────────────────────────────────────────────────
The design calls for embeddings (`nomic-embed-text` on local ollama, vectors in
qdrant, zero API cost). Both are reachable on this host and both return **401**
— they need API keys we do not have. So the designed fallback runs:

    "Lexical fallback if a side-car is down — discovery NEVER fails because
     ollama or qdrant is unavailable."

That clause was written before we knew we would need it on day one. Building
the fallback first means the funnel works now and gets better later, rather
than being blocked on a credential.

────────────────────────────────────────────────────────────────────────────
IT RUNS OFF THE EVENT LOOP
────────────────────────────────────────────────────────────────────────────
Pimlico's clustering took **181 seconds for 1,690 signals** inline. It froze
HTTP, and the run outlived its own lease. Everything expensive here is inside
`asyncio.to_thread`.

────────────────────────────────────────────────────────────────────────────
THE ADMISSION RULE IS LOAD-BEARING
────────────────────────────────────────────────────────────────────────────
A concept needs ≥4 content words. Bare brand names and one-word review titles
embed (and lexically match) as mutually similar; in Pimlico they formed a
20-member false cluster that would have cleared every gate and auto-built
garbage.
"""
from __future__ import annotations

import asyncio
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Optional

import structlog

from .. import db

log = structlog.get_logger("discovery.cluster")

WINDOW_DAYS = 30
MIN_CLUSTER_SIZE = 2
# Fallback only. The live value is a `discovery_params` row — see `params()`.
# 0.28 was an unmeasured guess and produced ZERO cross-source clusters.
SIMILARITY_THRESHOLD = 0.18


async def params() -> dict[str, float]:
    """Clustering parameters, from the database.

    Same rule as gate thresholds, price ratios and source queries: anything
    tunable is a row, so retuning is an UPDATE rather than a redeploy.
    """
    try:
        rows = await db.fetch("SELECT param, value FROM discovery_params")
        return {r["param"]: float(r["value"]) for r in rows}
    except Exception:                                            # noqa: BLE001
        return {}

# Words that carry no discriminating signal. Kept short and explicit rather
# than pulling an NLP dependency for a list of 40 words.
STOP = frozenset("""
a an the and or but if then than that this these those there here is are was were be been being
am do does did doing have has had having i you he she it we they me him her us them my your his
its our their of in on at to for with without from by as into over under about after before
so such no not only own same too very can will just don should now what which who whom when where
why how all any both each few more most other some
""".split())

_WORD = re.compile(r"[a-z][a-z0-9'+-]{1,}")

# Longest first — "ments" must be tried before "ment" before "s".
_SUFFIXES = ("ization", "ational", "ations", "ements", "ement", "ments",
             "ation", "ingly", "ings", "ance", "ence", "ment", "ing",
             "ies", "ers", "est", "ed", "es", "al", "er", "ly", "s")

MIN_STEM = 4


def stem(word: str) -> str:
    """Light suffix stripping. Not linguistics — just enough to make related
    words collide.

    🔴 WITHOUT THIS THE FUNNEL CANNOT CROSS-CORROBORATE, AND THAT WAS MEASURED.

    "best way to RECONCILE credit card statements" (search) and
    "implement payment RECONCILIATION, refunds and INVOICES" (community) are
    obviously about the same problem, and shared **zero** tokens:

        reconcile / reconciliation / reconciling  → three different terms
        invoice / invoices                        → two different terms

    So every cross-source cluster count was 0, at every similarity threshold
    from 0.30 down to 0.08 — where clustering already over-merges into
    27-member blobs. It looked like a threshold problem and was a tokenisation
    problem. Both of those words now stem to "reconcil".
    """
    w = word
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= MIN_STEM:
            w = w[: -len(suf)]
            break
    # Collapse trailing vowels so reconcile/reconcili both reach reconcil.
    while len(w) > MIN_STEM and w[-1] in "eiy":
        w = w[:-1]
    return w


def content_words(text: str) -> list[str]:
    """Lowercase, stemmed content words; stopwords and 1–2 char tokens removed."""
    return [stem(w) for w in _WORD.findall((text or "").lower())
            if len(w) > 2 and w not in STOP]


def admissible(concept: str) -> bool:
    """≥4 content words. See the module docstring — this is not a nicety."""
    return len(set(content_words(concept))) >= 4


@dataclass
class Doc:
    signal_id: int
    concept: str
    source_type: str
    source_name: str
    voice_id: Optional[int]
    terms: set[str] = field(default_factory=set)


@dataclass
class Cluster:
    members: list[Doc]
    terms: list[str]

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def source_types(self) -> set[str]:
        return {d.source_type for d in self.members}

    @property
    def distinct_voices(self) -> int:
        """🔴 The denominator that matters.

        Five mentions from five people is a market; five mentions from one
        person is one loud person. Pimlico's frequency gate could not tell those
        apart, and this distinction exists only because A1d captured the author.
        """
        return len({d.voice_id for d in self.members if d.voice_id is not None})

    @property
    def label(self) -> str:
        return " / ".join(self.terms[:4]) or "unlabelled"


async def load_window(days: int = WINDOW_DAYS) -> list[Doc]:
    """Signals inside the rolling window, with their source type and author."""
    rows = await db.fetch(
        """
        SELECT g.id, g.concept, s.source_type, s.name AS source_name,
               (SELECT vm.voice_id FROM voice_mentions vm
                 WHERE vm.signal_id = g.id LIMIT 1) AS voice_id
          FROM signals g
          JOIN sources s ON s.id = g.source_id
         WHERE g.observed_at > now() - make_interval(days => $1)
         ORDER BY g.id
        """, days)

    docs: list[Doc] = []
    skipped = 0
    for r in rows:
        if not admissible(r["concept"]):
            skipped += 1
            continue
        docs.append(Doc(signal_id=r["id"], concept=r["concept"],
                        source_type=r["source_type"], source_name=r["source_name"],
                        voice_id=r["voice_id"],
                        terms=set(content_words(r["concept"]))))
    if skipped:
        log.info("cluster.inadmissible_skipped", skipped=skipped, admitted=len(docs))
    return docs


def _idf(docs: list[Doc]) -> dict[str, float]:
    """Inverse document frequency, so shared rare words dominate similarity.

    Without it, two documents both containing "software" and "work" look
    related. With it, two documents sharing "reconcile" and "subcontractor" do —
    and that is the difference between a market and a coincidence.
    """
    n = len(docs)
    df = Counter()
    for d in docs:
        df.update(d.terms)
    return {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}


def _similarity(a: Doc, b: Doc, idf: dict[str, float]) -> float:
    """idf-weighted OVERLAP COEFFICIENT (Szymkiewicz–Simpson).

        shared_weight / min(weight_a, weight_b)

    🔴 Third metric, and the reasoning is worth keeping because the first two
    were wrong in instructive ways.

    · **Jaccard** normalises by the UNION, so a 6-word search phrase and a
      60-word App Store review scored near zero even when both were plainly
      about invoice matching — the review's 50 unrelated words dominated the
      denominator. Seven review documents carrying the only real pain evidence
      in the corpus sat outside every cluster because of this, leaving every
      cross-source cluster at severity 0.0.
    · **Cosine** normalises by the geometric mean, which is better but still
      symmetric: 2 shared terms out of 60 stays small however you divide it.
      Measured, it produced FEWER clusters than Jaccard, not more.

    The question actually being asked is *containment*: how much of the SHORTER
    document is accounted for by the overlap? A search phrase is a compressed
    statement of a need; a review is a verbose one. Normalising by the smaller
    of the two is the metric that matches the question.

    The over-merge risk (a tiny document fully contained in a large one scoring
    1.0) is held off by the ≥4-content-word admission rule and by idf weighting,
    which makes common words contribute almost nothing.
    """
    shared = a.terms & b.terms
    if not shared:
        return 0.0
    num = sum(idf.get(t, 1.0) for t in shared)
    wa = sum(idf.get(t, 1.0) for t in a.terms)
    wb = sum(idf.get(t, 1.0) for t in b.terms)
    smaller = min(wa, wb)
    return num / smaller if smaller else 0.0


def _cluster_sync(docs: list[Doc], threshold: float) -> list[Cluster]:
    """Single-link agglomeration via union-find. Runs in a worker thread.

    O(n²) on pairs, which is fine at the scale the window enforces (hundreds).
    If the window ever holds tens of thousands this needs an inverted index —
    but pretending to need that now would be the wrong kind of foresight.
    """
    idf = _idf(docs)
    parent = list(range(len(docs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(len(docs)):
        for j in range(i + 1, len(docs)):
            if _similarity(docs[i], docs[j], idf) >= threshold:
                union(i, j)

    groups: dict[int, list[Doc]] = {}
    for idx, d in enumerate(docs):
        groups.setdefault(find(idx), []).append(d)

    out = []
    for members in groups.values():
        if len(members) < MIN_CLUSTER_SIZE:
            continue
        shared = Counter()
        for m in members:
            shared.update(m.terms)
        # Terms present in most members, ranked by idf — the cluster's label.
        terms = [t for t, c in shared.most_common()
                 if c >= max(2, len(members) // 2)]
        terms.sort(key=lambda t: -idf.get(t, 1.0))
        out.append(Cluster(members=members, terms=terms[:8]))

    out.sort(key=lambda c: -c.size)
    return out


async def cluster(docs: list[Doc], threshold: Optional[float] = None) -> list[Cluster]:
    """Cluster off the event loop.

    Pimlico ran this inline: 181s for 1,690 signals, HTTP frozen, lease expired
    underneath it. `to_thread` is not an optimisation, it is what stops the
    scheduler losing the run it is executing.
    """
    if not docs:
        return []
    if threshold is None:
        threshold = (await params()).get("similarity_threshold", SIMILARITY_THRESHOLD)
    log.info("cluster.start", docs=len(docs), threshold=threshold)
    return await asyncio.to_thread(_cluster_sync, docs, float(threshold))


async def persist(clusters: list[Cluster], run_id: Optional[int] = None) -> list[int]:
    """Write clusters and point their signals at them."""
    ids: list[int] = []
    import json
    for c in clusters:
        cid = await db.fetchval(
            """
            INSERT INTO clusters (label, member_count, method, terms, source_types,
                                  distinct_voices, run_id, first_seen, last_seen)
            VALUES ($1,$2,'lexical',$3::jsonb,$4::jsonb,$5,$6, now(), now())
            RETURNING id
            """,
            c.label[:200], c.size, json.dumps(c.terms),
            json.dumps(sorted(c.source_types)), c.distinct_voices, run_id)
        await db.execute(
            "UPDATE signals SET cluster_id = $1 WHERE id = ANY($2::bigint[])",
            int(cid), [m.signal_id for m in c.members])
        ids.append(int(cid))
    log.info("cluster.persisted", clusters=len(ids),
             largest=max((c.size for c in clusters), default=0))
    return ids
