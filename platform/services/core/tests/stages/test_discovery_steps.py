"""Stage tests for the Phase A discovery steps.

The `@step` decorator refuses to register a step whose declared test file does
not exist, so this file existing is a precondition for the steps existing at
all. That ordering is the point of C2: the test is not an afterthought, it is
part of the registration contract.
"""
from __future__ import annotations

import pytest

from jarvis import db
from jarvis.discovery import steps as dsteps
from jarvis.runtime import engine, registry
from jarvis.runtime.types import StepStatus


async def test_the_discovery_steps_are_registered(clean_db):
    """`jpd steps` stops being empty here."""
    dsteps.register()
    ids = set(registry.all_steps())
    for expected in ("discovery.normalise", "discovery.cluster", "discovery.gate",
                     "discovery.qualify", "discovery.score", "discovery.promote"):
        assert expected in ids, f"{expected} is not registered"
    assert registry.validate_registry() == []


async def test_every_step_declares_an_acceptance_predicate(clean_db):
    dsteps.register()
    for sid, spec in registry.all_steps().items():
        assert callable(spec.acceptance), f"{sid} has no acceptance predicate"
        assert spec.acceptance_desc, f"{sid} has no readable acceptance description"


async def test_normalise_fails_when_the_window_is_empty(clean_db):
    """An empty window is a real state and must FAIL the step rather than
    succeed with nothing — a funnel that reports success on zero input is how
    Pimlico ran for three weeks without anyone noticing."""
    dsteps.register()
    ctx = await engine.open_context(await engine.create_run("DISCOVER"))
    r = await engine.execute("discovery.normalise", ctx)
    assert r.status is StepStatus.FAILED
    assert "no admissible signals" in (r.reason or "").lower()


async def test_a_cross_source_need_is_promoted_autonomously(clean_db):
    """🔴 THE PHASE-4 EXIT CRITERION, with controlled data.

    "One need promoted autonomously from >= 2 source types."

    Pimlico's discovery has never promoted a single need autonomously. This is
    the whole difference, so it gets a deterministic test rather than relying
    on whatever the live corpus happens to contain today.
    """
    from jarvis.discovery import steps as ds

    # 🔴 The test sets its OWN similarity threshold, deliberately.
    #
    # It is verifying the promotion MECHANISM — cluster, gate, qualify, score,
    # promote — not the calibration. Leaving it coupled to the production-tuned
    # value meant every recalibration broke this test for reasons unrelated to
    # its subject (it happened twice while writing it: once at 0.18, once at
    # 0.42, where the fixture scored 0.40). Calibration is validated by
    # measurement against the real corpus; the mechanism is validated here.
    await db.execute(
        "UPDATE discovery_params SET value = 0.30 WHERE param = 'similarity_threshold'")

    # Two source types describing the SAME problem, with pain language and
    # commercial intent, from five distinct voices.
    search_id = await db.fetchval("SELECT id FROM sources WHERE name='google_suggest'")
    review_id = await db.fetchval("SELECT id FROM sources WHERE name='app_store_reviews'")

    async def add(source_id, ext, concept, voice=None):
        sid = await db.fetchval(
            "INSERT INTO signals (source_id, external_id, concept, observed_at) "
            "VALUES ($1,$2,$3, now()) RETURNING id", source_id, ext, concept)
        if voice:
            vid = await db.fetchval(
                "INSERT INTO voices (kind, display_name, handle, platform) "
                "VALUES ('person',$1,$1,'test') ON CONFLICT (platform, handle) "
                "DO UPDATE SET last_seen=now() RETURNING id", voice)
            await db.execute(
                "INSERT INTO voice_mentions (voice_id, signal_id, stance) "
                "VALUES ($1,$2,'reports_pain')", vid, sid)
        return sid

    # Background noise FIRST. Without it every document shares the key terms,
    # so idf correctly weights them to nothing and the fixture cannot cluster —
    # the first version of this test failed for exactly that reason. A corpus
    # in which the signal words are universal is not a realistic corpus.
    community_id = await db.fetchval("SELECT id FROM sources WHERE name='hacker_news'")
    noise = [
        "kubernetes operator crashloop on node draining during upgrade",
        "typescript generic inference fails with conditional mapped types",
        "postgres autovacuum tuning for high write throughput tables",
        "rust borrow checker lifetime elision in async trait methods",
        "webpack bundle splitting strategy for large single page apps",
        "grafana dashboard variable interpolation across data sources",
        "terraform state locking with dynamodb backend concurrency",
        "kafka consumer group rebalancing storm under partition churn",
    ]
    for i, text in enumerate(noise):
        await add(community_id, f"n{i}", text)

    for i in range(3):
        await add(search_id, f"s{i}",
                  f"best way to reconcile supplier invoices automatically {i}")
    for i in range(5):
        await add(review_id, f"r{i}",
                  "reconciling supplier invoices is a nightmare, we waste hours "
                  f"every month and it costs us real money to fix {i}",
                  voice=f"buyer{i}")

    out = await ds.run_funnel()

    assert out["promoted"], (
        f"nothing promoted; stopped at {out.get('stopped_at')}; "
        f"steps={ {k: v['status'] for k, v in out['steps'].items()} }")

    need = await db.fetchrow(
        "SELECT n.*, c.source_types FROM needs n JOIN clusters c ON c.id=n.cluster_id "
        "WHERE n.id = $1", out["promoted"][0])
    assert need["status"] == "promoted"
    assert need["promoted_by"] == "auto", "promotion must be AUTONOMOUS"
    assert int(need["cross_source"]) >= 2, "must span >= 2 distinct source types"
    assert need["gap"] is None, "gap is deferred to Phase B — never invented in Phase A"
    assert float(need["severity"]) >= 4.0


async def _seed_reconcile_corpus(batch: int) -> None:
    """The exit-criterion fixture, re-seedable: same pain, fresh external ids
    and fresh voices per batch — which is exactly what a nightly harvest
    delivers when the same problem keeps being talked about."""
    search_id = await db.fetchval("SELECT id FROM sources WHERE name='google_suggest'")
    review_id = await db.fetchval("SELECT id FROM sources WHERE name='app_store_reviews'")
    community_id = await db.fetchval("SELECT id FROM sources WHERE name='hacker_news'")

    async def add(source_id, ext, concept, voice=None):
        sid = await db.fetchval(
            "INSERT INTO signals (source_id, external_id, concept, observed_at) "
            "VALUES ($1,$2,$3, now()) RETURNING id", source_id, ext, concept)
        if voice:
            vid = await db.fetchval(
                "INSERT INTO voices (kind, display_name, handle, platform) "
                "VALUES ('person',$1,$1,'test') ON CONFLICT (platform, handle) "
                "DO UPDATE SET last_seen=now() RETURNING id", voice)
            await db.execute(
                "INSERT INTO voice_mentions (voice_id, signal_id, stance) "
                "VALUES ($1,$2,'reports_pain')", vid, sid)

    noise = [
        "kubernetes operator crashloop on node draining during upgrade",
        "typescript generic inference fails with conditional mapped types",
        "postgres autovacuum tuning for high write throughput tables",
        "rust borrow checker lifetime elision in async trait methods",
        "webpack bundle splitting strategy for large single page apps",
        "grafana dashboard variable interpolation across data sources",
        "terraform state locking with dynamodb backend concurrency",
        "kafka consumer group rebalancing storm under partition churn",
    ]
    for i, text in enumerate(noise):
        await add(community_id, f"b{batch}n{i}", text)
    for i in range(3):
        await add(search_id, f"b{batch}s{i}",
                  f"best way to reconcile supplier invoices automatically {i}")
    for i in range(5):
        await add(review_id, f"b{batch}r{i}",
                  "reconciling supplier invoices is a nightmare, we waste hours "
                  f"every month and it costs us real money to fix {i}",
                  voice=f"buyer{batch}-{i}")


async def test_rerunning_the_funnel_does_not_repromote_the_same_need(clean_db):
    """🔴 THE DEDUP TEST. Clusters are re-created every run, so by 2026-08-16
    six rows in `needs` were TWO actual needs — and an autonomous scheduler
    would have paid for research and forge on every copy.

    A re-surfaced pain must attach its new voices to the EXISTING need, not
    mint a new one."""
    from jarvis.discovery import steps as ds

    await db.execute(
        "UPDATE discovery_params SET value = 0.30 WHERE param = 'similarity_threshold'")

    await _seed_reconcile_corpus(1)
    out1 = await ds.run_funnel()
    assert out1["promoted"], "fixture must promote on the first run"
    need_id = out1["promoted"][0]
    needs_before = await db.fetchval("SELECT count(*) FROM needs")
    voices_before = await db.fetchval(
        "SELECT count(DISTINCT voice_id) FROM voice_mentions WHERE need_id = $1",
        need_id)

    await _seed_reconcile_corpus(2)
    out2 = await ds.run_funnel()

    assert await db.fetchval("SELECT count(*) FROM needs") == needs_before, (
        f"second run minted a duplicate need: promoted={out2.get('promoted')}")
    voices_after = await db.fetchval(
        "SELECT count(DISTINCT voice_id) FROM voice_mentions WHERE need_id = $1",
        need_id)
    assert voices_after > voices_before, (
        "the re-surfaced pain's new voices must attach to the existing need")


def test_title_key_is_order_insensitive():
    """The same cluster surfaced as 'payabl / automat / account' one run and
    'automat / payabl / account' the next — token order is not identity."""
    from jarvis.discovery.funnel import _title_key
    assert _title_key("payabl / automat / account") == \
        _title_key("automat / payabl / account")
    assert _title_key("a / b") != _title_key("a / c")


async def test_the_census_records_every_evaluation_pass_and_fail(clean_db):
    """C6. Pimlico's census lived in process memory and was lost on every
    restart, so three weeks of zero promotions were undiagnosable."""
    from jarvis.discovery import gates, steps as ds

    src = await db.fetchval("SELECT id FROM sources WHERE name='google_suggest'")
    for i in range(4):
        await db.execute(
            "INSERT INTO signals (source_id, external_id, concept, observed_at) "
            "VALUES ($1,$2,$3, now())", src, f"c{i}",
            f"how to reconcile supplier invoices in accounting software {i}")

    await ds.run_funnel()

    rows = await db.fetch("SELECT DISTINCT gate FROM gate_evaluations")
    assert {r["gate"] for r in rows} >= {"frequency", "severity", "cross_source"}
    # Failures are recorded too — that is the entire point.
    assert await db.fetchval(
        "SELECT count(*) FROM gate_evaluations WHERE passed = FALSE") > 0

    # Override EVERY gate that blocked, not a guessed subset — the first
    # version of this test overrode severity and cross_source and was still
    # blocked by frequency, which made a working replay look broken.
    blocked = {b["gate"] for b in await gates.blocking_gate()}
    overrides = {g: 0.0 for g in blocked if g != "recency_days"}
    replay = await gates.replay(overrides)
    assert replay["clusters_evaluated"] > 0
    assert replay["would_promote_count"] >= 1, (
        f"counterfactual replay must show what looser thresholds would have "
        f"promoted; overrides={overrides} still_blocked={replay['still_blocked_by']}")
