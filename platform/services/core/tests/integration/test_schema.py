"""Schema constraints, tested as the features they are.

Each constraint below makes a specific Pimlico bug *unrepresentable*. A
constraint nobody tests is a constraint that gets dropped by a later migration
and missed — which is precisely the "check the constraint, not just the width"
lesson this host taught three separate times.
"""
from __future__ import annotations

import pytest

from jarvis import db


async def test_migrations_are_idempotent(clean_db):
    report = await db.migrate()
    assert all(r["action"] == "already_applied" for r in report)


async def test_migration_drift_is_refused_not_papered_over(clean_db, monkeypatch):
    """If a shipped migration's content changes, re-running must REFUSE.

    Either the file reverted (C8 — observed on this host, mechanism still
    unidentified) or someone edited a shipped migration. Both need a human.
    """
    await db.execute(
        "UPDATE schema_migrations SET sha256 = 'not-the-real-hash' WHERE version = $1",
        "001_core.sql")
    with pytest.raises(db.MigrationDrift, match="Refusing to continue"):
        await db.migrate()
    # restore so the rest of the session works
    import hashlib
    real = hashlib.sha256((db.MIGRATIONS_DIR / "001_core.sql").read_bytes()).hexdigest()
    await db.execute("UPDATE schema_migrations SET sha256 = $1 WHERE version = $2",
                     real, "001_core.sql")


async def test_a_claim_cannot_exist_without_evidence(clean_db):
    """C4, and the single most important constraint in the schema.

    A deliverable with an uncited factual claim cannot be published because
    the DATABASE refuses it. Pimlico had no citation field anywhere and sold
    27.5k-word products that were pure model recall.
    """
    need = await db.fetchval("INSERT INTO needs (title) VALUES ('t') RETURNING id")
    sol = await db.fetchval(
        "INSERT INTO solutions (need_id, title) VALUES ($1,'s') RETURNING id", need)
    art = await db.fetchval(
        "INSERT INTO artifacts (solution_id, tier, kind, sha256, bytes, storage_uri) "
        "VALUES ($1,'roadmap','pdf','abc',1,'file:///x') RETURNING id", sol)

    with pytest.raises(Exception):
        await db.execute(
            "INSERT INTO claims (deliverable_id, text, evidence_id) VALUES ($1,'x',NULL)", art)

    ev = await db.fetchval(
        "INSERT INTO evidence (sha256, url) VALUES ('h','https://x.test') RETURNING id")
    cid = await db.fetchval(
        "INSERT INTO claims (deliverable_id, text, evidence_id) VALUES ($1,'x',$2) RETURNING id",
        art, ev)
    assert cid


async def test_evidence_backing_a_claim_cannot_be_deleted(clean_db):
    """ON DELETE RESTRICT: a published claim's citation must not evaporate."""
    need = await db.fetchval("INSERT INTO needs (title) VALUES ('t') RETURNING id")
    sol = await db.fetchval(
        "INSERT INTO solutions (need_id, title) VALUES ($1,'s') RETURNING id", need)
    art = await db.fetchval(
        "INSERT INTO artifacts (solution_id, tier, kind, sha256, bytes, storage_uri) "
        "VALUES ($1,'roadmap','pdf','abc',1,'file:///x') RETURNING id", sol)
    ev = await db.fetchval("INSERT INTO evidence (sha256) VALUES ('h') RETURNING id")
    await db.execute(
        "INSERT INTO claims (deliverable_id, text, evidence_id) VALUES ($1,'x',$2)", art, ev)

    with pytest.raises(Exception):
        await db.execute("DELETE FROM evidence WHERE id = $1", ev)


async def test_prices_are_integers_in_minor_units(clean_db):
    """Pimlico listed every one of its products at 100x because a float
    euro/cent confusion went unnoticed until a read-back caught it.

    price_minor is BIGINT. 2.97 cannot be stored as 2.97 — it is either 297
    or it is a type error, and both are better than silently wrong.
    """
    need = await db.fetchval("INSERT INTO needs (title) VALUES ('t') RETURNING id")
    sol = await db.fetchval(
        "INSERT INTO solutions (need_id, title) VALUES ($1,'s') RETURNING id", need)

    oid = await db.fetchval(
        "INSERT INTO offers (solution_id, tier, price_minor) VALUES ($1,'roadmap',29700) "
        "RETURNING id", sol)
    assert isinstance(await db.fetchval("SELECT price_minor FROM offers WHERE id=$1", oid), int)

    with pytest.raises(Exception):
        await db.execute(
            "INSERT INTO offers (solution_id, tier, price_minor) VALUES ($1,'instructions',0)", sol)


async def test_one_offer_per_solution_and_tier(clean_db):
    need = await db.fetchval("INSERT INTO needs (title) VALUES ('t') RETURNING id")
    sol = await db.fetchval(
        "INSERT INTO solutions (need_id, title) VALUES ($1,'s') RETURNING id", need)
    await db.execute(
        "INSERT INTO offers (solution_id, tier, price_minor) VALUES ($1,'roadmap',100)", sol)
    with pytest.raises(Exception):
        await db.execute(
            "INSERT INTO offers (solution_id, tier, price_minor) VALUES ($1,'roadmap',200)", sol)


async def test_an_order_is_unique_per_provider_reference(clean_db):
    """Idempotent fulfilment starts here: a replayed webhook must not mint a
    second entitlement."""
    need = await db.fetchval("INSERT INTO needs (title) VALUES ('t') RETURNING id")
    sol = await db.fetchval(
        "INSERT INTO solutions (need_id, title) VALUES ($1,'s') RETURNING id", need)
    off = await db.fetchval(
        "INSERT INTO offers (solution_id, tier, price_minor) VALUES ($1,'roadmap',100) "
        "RETURNING id", sol)
    args = (off, "b@x.test", "buyer1", 100, "EUR", "stripe", "evt_1")
    await db.execute(
        "INSERT INTO orders (offer_id, buyer_email, buyer_ref, amount_minor, currency, "
        "provider, provider_ref) VALUES ($1,$2,$3,$4,$5,$6,$7)", *args)
    with pytest.raises(Exception):
        await db.execute(
            "INSERT INTO orders (offer_id, buyer_email, buyer_ref, amount_minor, currency, "
            "provider, provider_ref) VALUES ($1,$2,$3,$4,$5,$6,$7)", *args)


async def test_signature_valid_defaults_to_false(clean_db):
    """The safe direction. A failed signature cannot fulfil, and an UNCHECKED
    signature must not masquerade as a checked one."""
    need = await db.fetchval("INSERT INTO needs (title) VALUES ('t') RETURNING id")
    sol = await db.fetchval(
        "INSERT INTO solutions (need_id, title) VALUES ($1,'s') RETURNING id", need)
    off = await db.fetchval(
        "INSERT INTO offers (solution_id, tier, price_minor) VALUES ($1,'roadmap',100) "
        "RETURNING id", sol)
    oid = await db.fetchval(
        "INSERT INTO orders (offer_id, buyer_ref, amount_minor, currency, provider, provider_ref) "
        "VALUES ($1,'b',100,'EUR','stripe','evt_2') RETURNING id", off)
    row = await db.fetchrow("SELECT signature_valid, amount_matched, status FROM orders WHERE id=$1", oid)
    assert row["signature_valid"] is False
    assert row["amount_matched"] is False
    assert row["status"] == "received"


async def test_do_not_contact_defaults_to_true(clean_db):
    """DEC-004, non-negotiable. Community-sourced authors are evidence, never
    a mailing list. Getting this wrong is a legal problem, not a growth tactic.
    """
    vid = await db.fetchval(
        "INSERT INTO voices (kind, display_name, platform) VALUES "
        "('person','someone','reddit') RETURNING id")
    row = await db.fetchrow("SELECT do_not_contact, contactable FROM voices WHERE id=$1", vid)
    assert row["do_not_contact"] is True
    assert row["contactable"] is False


async def test_stance_vocabulary_is_constrained(clean_db):
    vid = await db.fetchval(
        "INSERT INTO voices (kind, display_name, platform) VALUES "
        "('company','ACME','sec') RETURNING id")
    await db.execute(
        "INSERT INTO voice_mentions (voice_id, stance, quote) VALUES ($1,'reports_pain','x')", vid)
    with pytest.raises(Exception):
        await db.execute(
            "INSERT INTO voice_mentions (voice_id, stance) VALUES ($1,'vibes')", vid)


async def test_evidence_source_kind_vocabulary(clean_db):
    """'paraphrase' must be storable — it is what the publish predicate rejects
    (DEC-003). And nothing outside the vocabulary may be stored at all."""
    assert await db.fetchval(
        "INSERT INTO evidence (sha256, source_kind) VALUES ('h','paraphrase') RETURNING id")
    with pytest.raises(Exception):
        await db.execute("INSERT INTO evidence (sha256, source_kind) VALUES ('h','summary')")


async def test_step_status_check_covers_every_enum_member(clean_db):
    """"Check the constraint, not just the width" — the trap this host sprang
    three times. Every StepStatus value must be insertable, and nothing else."""
    from jarvis.runtime.types import StepStatus

    run_id = await db.fetchval(
        "INSERT INTO runs (phase) VALUES ('TEST') RETURNING id")
    for s in StepStatus:
        assert await db.fetchval(
            "INSERT INTO steps (run_id, step_id, phase, status) "
            "VALUES ($1,'x','TEST',$2) RETURNING id", run_id, s.value)

    for bad in ("unknown", "", "SUCCEEDED", "done"):
        with pytest.raises(Exception):
            await db.execute(
                "INSERT INTO steps (run_id, step_id, phase, status) VALUES ($1,'x','TEST',$2)",
                run_id, bad)


async def test_gate_thresholds_are_data_not_constants(clean_db):
    """Retuning a gate must be an UPDATE, not a redeploy. Pimlico's thresholds
    were constants, so calibration required shipping code."""
    rows = {r["gate"]: float(r["threshold"]) for r in
            await db.fetch("SELECT gate, threshold FROM gate_thresholds")}
    for g in ("frequency", "severity", "cross_source", "recency_days",
              "commercial_intent", "distinct_voices"):
        assert g in rows, f"gate {g} not seeded"
    assert rows["severity"] == 4.0
    assert rows["distinct_voices"] == 3.0


async def test_authority_sources_share_one_source_type(clean_db):
    """Load-bearing gate rule: authority sources are opinions, amplified.

    All creator channels share source_type='authority' precisely so they can
    never self-corroborate the cross-source gate. If one of them were given
    its own type, a single influencer could clear the gate alone.
    """
    rows = await db.fetch(
        "SELECT name, source_type FROM sources WHERE name LIKE 'yt_%' OR name IN "
        "('skool','tubeonai')")
    assert rows, "authority sources not seeded"
    assert {r["source_type"] for r in rows} == {"authority"}
