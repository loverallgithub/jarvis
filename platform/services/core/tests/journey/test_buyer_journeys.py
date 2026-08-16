"""THE JOURNEY TESTS — blocking on every commerce deploy.

Three buyer journeys and the upgrade, end to end, against a real database and
the real money path. Only the *provider* is a double; signature verification,
the amount check, idempotency, entitlement, artifact existence, token minting,
redemption and notification are all production code.

This is the file that answers the question Pimlico still cannot: **can we take
money and deliver the thing?** Pimlico has nine live products, real PDFs,
working sales pages, working checkout links, and zero orders ever — with
nowhere in the schema to record one if it happened.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from jarvis import db
from jarvis.commerce import delivery, fulfilment, notify, offers, orders, pricing
from jarvis.commerce.providers import base as providers
from jarvis.commerce.providers.stub import StubProvider

BASE_MINOR = 29700          # €297 roadmap anchor


@pytest.fixture
async def stub(clean_db):
    providers._reset_for_tests()
    p = StubProvider()
    providers.register(p)
    yield p
    providers._reset_for_tests()


@pytest.fixture
async def solution(clean_db, tmp_path):
    """A solution with all three tier artifacts really on disk."""
    need = await db.fetchval(
        "INSERT INTO needs (title, status) VALUES ('Contractor change-orders','promoted') "
        "RETURNING id")
    sol = await db.fetchval(
        "INSERT INTO solutions (need_id, title) VALUES ($1,'Change-Order Recovery') "
        "RETURNING id", need)

    artifacts = {}
    for tier in pricing.TIERS:
        f = tmp_path / f"{tier}.pdf"
        content = f"%PDF-1.4 {tier} content for solution {sol}\n".encode() * 40
        f.write_bytes(content)
        # offerable = TRUE: these stand for artifacts that PASSED verification.
        # `publish` refuses a ladder whose deliverables were withheld, so a
        # fixture that left this at its FALSE default would be modelling a
        # product that must not be sold — see the gate tests at the bottom.
        aid = await db.fetchval(
            "INSERT INTO artifacts (solution_id, tier, kind, sha256, bytes, storage_uri,"
            "                       structural_ok, factual_ok, offerable) "
            "VALUES ($1,$2,'pdf',$3,$4,$5,TRUE,TRUE,TRUE) RETURNING id",
            sol, tier, hashlib.sha256(content).hexdigest(), len(content), f"file://{f}")
        artifacts[tier] = aid
    return {"need_id": need, "solution_id": sol, "artifacts": artifacts}


async def _ladder(stub, solution):
    created = await offers.create_ladder(
        solution["solution_id"], base_minor=BASE_MINOR, provider="stub",
        store_id="jpd_test_store")
    await offers.publish(solution["solution_id"])
    return {c.tier: c for c in created}


async def _buy(stub, offer, *, provider_ref: str, buyer="buyer_1",
               amount_minor=None, sign=True):
    import json
    payload = stub.payment_payload(
        external_ref=offer.external_ref,
        amount_minor=amount_minor if amount_minor is not None else offer.price_minor,
        provider_ref=provider_ref, buyer_ref=buyer)
    raw = json.dumps(payload).encode()
    headers = stub.sign(raw) if sign else {}
    return await orders.receive("stub", raw, headers)


# ===========================================================================
# JOURNEY 1-3 — the three tiers
# ===========================================================================

@pytest.mark.parametrize("tier,expected_tiers", [
    ("roadmap",      ["roadmap"]),
    ("instructions", ["roadmap", "instructions"]),
    ("deployed",     ["roadmap", "instructions", "deployed"]),
])
async def test_buyer_journey(stub, solution, tier, expected_tiers):
    """order → entitlement → fulfilment → delivery → notification.

    Each tier is a SUPERSET of the one below, so an Instructions buyer receives
    the Roadmap too. Delivering only the top rung would shortchange the buyer
    silently, via the data model rather than via a bug anyone would notice.
    """
    L = await _ladder(stub, solution)
    offer = L[tier]

    r = await _buy(stub, offer, provider_ref=f"txn_{tier}")
    assert r.accepted, r.reason
    assert r.order_id and r.entitlement_id

    order = await db.fetchrow("SELECT * FROM orders WHERE id=$1", r.order_id)
    assert order["signature_valid"] is True
    assert order["amount_matched"] is True
    assert order["amount_minor"] == offer.price_minor

    ent = await db.fetchrow("SELECT * FROM entitlements WHERE id=$1", r.entitlement_id)
    assert ent["tier"] == tier, "the tier must come from the OFFER, not the payload"

    fr = await fulfilment.fulfil(r.entitlement_id)
    assert fr.ok, fr.failed
    assert sorted(d["tier"] for d in fr.delivered) == sorted(expected_tiers)

    # Every delivered link must actually hand over bytes.
    for d in fr.delivered:
        path, meta = await delivery.redeem(d["token"])
        assert Path(path).is_file()
        assert Path(path).stat().st_size > 0

    n = await notify.send_delivery(r.entitlement_id, r.order_id, fr.delivered)
    # No channel connector is live in phase 1, so the honest outcome is
    # skipped_dormant — recorded as an OPEN OBLIGATION, never as sent.
    assert n.status == "skipped_dormant"
    owed = await notify.pending_and_failed()
    assert owed["owed"] >= 1


async def test_all_three_journeys_are_independent(stub, solution):
    """Three different buyers, three tiers, no interference."""
    L = await _ladder(stub, solution)
    results = {}
    for i, tier in enumerate(pricing.TIERS):
        r = await _buy(stub, L[tier], provider_ref=f"txn_multi_{i}", buyer=f"buyer_{i}")
        assert r.accepted, r.reason
        results[tier] = r
        fr = await fulfilment.fulfil(r.entitlement_id)
        assert fr.ok, fr.failed

    assert await db.fetchval("SELECT count(*) FROM orders") == 3
    assert await db.fetchval("SELECT count(*) FROM entitlements") == 3
    # roadmap 1 + instructions 2 + deployed 3
    assert await db.fetchval(
        "SELECT count(*) FROM fulfilments WHERE status='delivered'") == 6


# ===========================================================================
# JOURNEY 4 — the upgrade
# ===========================================================================

async def test_upgrade_delivers_only_the_delta(stub, solution):
    """G5. The artifact already exists, so the delta is almost pure margin —
    and re-sending everything would put our idempotency bugs in the buyer's
    inbox."""
    L = await _ladder(stub, solution)

    first = await _buy(stub, L["roadmap"], provider_ref="txn_up_1")
    fr1 = await fulfilment.fulfil(first.entitlement_id)
    assert [d["tier"] for d in fr1.delivered] == ["roadmap"]

    quote = await offers.upgrade_quote(first.entitlement_id, "deployed")
    assert quote["delta_minor"] == L["deployed"].price_minor - L["roadmap"].price_minor
    assert quote["delta_minor"] > 0

    second = await _buy(stub, L["deployed"], provider_ref="txn_up_2")
    await offers.record_upgrade(first.entitlement_id, "deployed",
                                quote["delta_minor"], second.order_id)

    fr2 = await fulfilment.fulfil_upgrade(first.entitlement_id, second.entitlement_id)
    assert fr2.ok, fr2.failed
    delivered = sorted(d["tier"] for d in fr2.delivered)
    assert delivered == ["deployed", "instructions"], \
        "an upgrade must deliver ONLY the tiers the buyer did not already have"
    assert all(d["tier"] != "roadmap" for d in fr2.delivered)
    assert await db.fetchval(
        "SELECT count(*) FROM fulfilments WHERE is_delta = TRUE") == 2


async def test_upgrade_delta_is_computed_from_what_was_actually_paid(stub, solution):
    """Not from current ratios. If the ladder is retuned after a purchase, the
    buyer's delta is still measured against the price they really paid."""
    L = await _ladder(stub, solution)
    first = await _buy(stub, L["roadmap"], provider_ref="txn_paid_1")

    await db.execute("UPDATE pricing_policy SET ratio_min=20, ratio_max=25 WHERE tier='deployed'")
    quote = await offers.upgrade_quote(first.entitlement_id, "deployed")
    assert quote["paid_minor"] == L["roadmap"].price_minor
    assert quote["target_minor"] == L["deployed"].price_minor


async def test_a_downgrade_is_refused(stub, solution):
    L = await _ladder(stub, solution)
    r = await _buy(stub, L["deployed"], provider_ref="txn_down")
    with pytest.raises(pricing.PricingError, match="not an upgrade"):
        await offers.upgrade_quote(r.entitlement_id, "roadmap")


# ===========================================================================
# ADVERSARIAL — every one of these is a Pimlico defect
# ===========================================================================

async def test_a_bad_signature_cannot_fulfil(stub, solution):
    """Pimlico's verifier shipped in observe mode and returned VALID when
    unconfigured. Reasonable for retro-fitting a live system; wrong for a new
    one. No valid signature, no order."""
    L = await _ladder(stub, solution)
    r = await _buy(stub, L["roadmap"], provider_ref="txn_bad_sig", sign=False)

    assert r.accepted is False
    assert "signature" in r.reason
    assert await db.fetchval("SELECT count(*) FROM orders") == 0
    assert await db.fetchval("SELECT count(*) FROM entitlements") == 0
    # …but the attempt IS recorded. "We never saw it" and "we rejected it" must
    # stay distinguishable months later.
    ev = await db.fetchrow("SELECT * FROM provider_events ORDER BY id DESC LIMIT 1")
    assert ev["signature_valid"] is False
    assert ev["accepted"] is False


async def test_an_underpaid_order_is_refused(stub, solution):
    """🔴 THE ONE THAT MATTERS MOST.

    Pimlico treated any `amount > 0` as a paid order, so a webhook claiming
    `amount: 1` would have minted a €297 product for one cent. The price comes
    from OUR offers row; the payload only says which offer.
    """
    L = await _ladder(stub, solution)
    r = await _buy(stub, L["deployed"], provider_ref="txn_underpaid", amount_minor=1)

    assert r.accepted is False
    assert "amount mismatch" in r.reason
    assert await db.fetchval("SELECT count(*) FROM orders") == 0
    assert await db.fetchval("SELECT count(*) FROM entitlements") == 0


async def test_an_overpaid_order_is_also_refused(stub, solution):
    """Overpayment is a mismatch too. It usually means the offer changed under
    the buyer, and silently keeping the extra is not a decision code should
    make."""
    L = await _ladder(stub, solution)
    r = await _buy(stub, L["roadmap"], provider_ref="txn_over",
                   amount_minor=L["roadmap"].price_minor + 5000)
    assert r.accepted is False
    assert "amount mismatch" in r.reason


async def test_a_replayed_webhook_changes_nothing(stub, solution):
    """Providers retry. The second delivery must find the existing order and
    do nothing — not a second entitlement, not a second delivery."""
    L = await _ladder(stub, solution)
    first = await _buy(stub, L["instructions"], provider_ref="txn_replay")
    assert first.accepted and not first.duplicate
    await fulfilment.fulfil(first.entitlement_id)

    second = await _buy(stub, L["instructions"], provider_ref="txn_replay")
    assert second.accepted is True
    assert second.duplicate is True
    assert second.order_id == first.order_id
    assert second.entitlement_id == first.entitlement_id

    assert await db.fetchval("SELECT count(*) FROM orders") == 1
    assert await db.fetchval("SELECT count(*) FROM entitlements") == 1

    again = await fulfilment.fulfil(first.entitlement_id)
    assert again.delivered == []
    assert sorted(again.already_delivered) == ["instructions", "roadmap"]
    assert await db.fetchval(
        "SELECT count(*) FROM fulfilments WHERE status='delivered'") == 2


async def test_an_unknown_product_is_refused(stub, solution):
    import json
    await _ladder(stub, solution)
    payload = stub.payment_payload(external_ref="stub_prod_9999", amount_minor=29700,
                                   provider_ref="txn_unknown")
    raw = json.dumps(payload).encode()
    r = await orders.receive("stub", raw, stub.sign(raw))
    assert r.accepted is False
    assert "no offer" in r.reason


async def test_a_payload_with_no_amount_is_unparseable_not_zero(stub, solution):
    """`payload.get("amount", 0)` is how a missing field becomes a free order.
    Absent is not zero."""
    import json
    L = await _ladder(stub, solution)
    raw = json.dumps({"transactionId": "t1", "productId": L["roadmap"].external_ref,
                      "contactId": "b"}).encode()
    r = await orders.receive("stub", raw, stub.sign(raw))
    assert r.accepted is False
    assert "unparseable" in r.reason
    assert await db.fetchval("SELECT count(*) FROM orders") == 0


async def test_an_offer_that_is_not_live_cannot_be_bought(stub, solution):
    L = await _ladder(stub, solution)
    await db.execute("UPDATE offers SET live = FALSE WHERE solution_id = $1",
                     solution["solution_id"])
    r = await _buy(stub, L["roadmap"], provider_ref="txn_not_live")
    assert r.accepted is False
    assert "not live" in r.reason


async def test_a_missing_artifact_fails_fulfilment_and_mints_no_token(stub, solution):
    """🔴 All three of Pimlico's delivery tokens point at files that do not
    exist. They were minted from an intention rather than a fact."""
    L = await _ladder(stub, solution)
    r = await _buy(stub, L["roadmap"], provider_ref="txn_missing")

    path = Path((await db.fetchval(
        "SELECT storage_uri FROM artifacts WHERE id=$1",
        solution["artifacts"]["roadmap"])).replace("file://", ""))
    path.unlink()

    fr = await fulfilment.fulfil(r.entitlement_id)
    assert fr.ok is False
    assert fr.failed and "no file at" in fr.failed[0]["reason"]
    assert await db.fetchval("SELECT count(*) FROM delivery_tokens") == 0, \
        "a token was minted against a file that does not exist"
    assert await db.fetchval(
        "SELECT count(*) FROM fulfilments WHERE status='failed'") == 1
    # And it is QUERYABLE, not merely logged.
    assert len(await fulfilment.undelivered_paid_orders()) == 1


async def test_an_empty_artifact_counts_as_missing(stub, solution):
    """A zero-byte PDF is not a product — and it is what a truncated write
    leaves behind."""
    L = await _ladder(stub, solution)
    r = await _buy(stub, L["roadmap"], provider_ref="txn_empty")
    path = Path((await db.fetchval(
        "SELECT storage_uri FROM artifacts WHERE id=$1",
        solution["artifacts"]["roadmap"])).replace("file://", ""))
    path.write_bytes(b"")

    fr = await fulfilment.fulfil(r.entitlement_id)
    assert fr.ok is False
    assert "empty" in fr.failed[0]["reason"]


async def test_partial_delivery_is_not_reported_as_success(stub, solution):
    """A buyer with two of three artifacts has a broken purchase."""
    L = await _ladder(stub, solution)
    r = await _buy(stub, L["deployed"], provider_ref="txn_partial")
    Path((await db.fetchval("SELECT storage_uri FROM artifacts WHERE id=$1",
                            solution["artifacts"]["instructions"])).replace("file://", "")).unlink()

    fr = await fulfilment.fulfil(r.entitlement_id)
    assert fr.ok is False
    assert fr.status == "partial"
    assert len(fr.delivered) == 2 and len(fr.failed) == 1


async def test_a_revoked_entitlement_cannot_be_fulfilled(stub, solution):
    L = await _ladder(stub, solution)
    r = await _buy(stub, L["roadmap"], provider_ref="txn_revoked")
    await db.execute("UPDATE entitlements SET revoked_at = now() WHERE id=$1",
                     r.entitlement_id)
    with pytest.raises(RuntimeError, match="revoked"):
        await fulfilment.fulfil(r.entitlement_id)


# ===========================================================================
# DELIVERY TOKENS
# ===========================================================================

async def test_tokens_are_stored_hashed_never_in_plaintext(stub, solution):
    """A download token is a bearer credential. Plaintext in the database makes
    a dump, a log line or a support screenshot into free inventory."""
    L = await _ladder(stub, solution)
    r = await _buy(stub, L["roadmap"], provider_ref="txn_hash")
    fr = await fulfilment.fulfil(r.entitlement_id)
    token = fr.delivered[0]["token"]

    stored = await db.fetchval("SELECT token_hash FROM delivery_tokens LIMIT 1")
    assert stored != token
    assert stored == hashlib.sha256(token.encode()).hexdigest()
    assert await db.fetchval(
        "SELECT count(*) FROM delivery_tokens WHERE token_hash = $1", token) == 0


async def test_download_limit_is_enforced(stub, solution):
    L = await _ladder(stub, solution)
    r = await _buy(stub, L["roadmap"], provider_ref="txn_limit")
    fr = await fulfilment.fulfil(r.entitlement_id)
    token = fr.delivered[0]["token"]
    await db.execute("UPDATE delivery_tokens SET max_downloads = 2")

    await delivery.redeem(token)
    await delivery.redeem(token)
    with pytest.raises(delivery.TokenInvalid):
        await delivery.redeem(token)


async def test_expired_and_revoked_tokens_are_refused(stub, solution):
    L = await _ladder(stub, solution)
    r = await _buy(stub, L["roadmap"], provider_ref="txn_exp")
    fr = await fulfilment.fulfil(r.entitlement_id)
    token = fr.delivered[0]["token"]

    await db.execute("UPDATE delivery_tokens SET expires_at = now() - interval '1 day'")
    with pytest.raises(delivery.TokenInvalid):
        await delivery.redeem(token)

    await db.execute("UPDATE delivery_tokens SET expires_at = now() + interval '1 day'")
    tid = await db.fetchval("SELECT id FROM delivery_tokens LIMIT 1")
    await delivery.revoke(tid, "test")
    with pytest.raises(delivery.TokenInvalid):
        await delivery.redeem(token)


async def test_an_unknown_token_is_refused(stub, solution):
    with pytest.raises(delivery.TokenInvalid):
        await delivery.redeem("this-token-was-never-issued")


async def test_the_sweep_finds_a_file_that_vanished_after_minting(stub, solution):
    """Mint-time checking cannot catch a file deleted afterwards. The buyer
    must never be the monitoring system."""
    L = await _ladder(stub, solution)
    r = await _buy(stub, L["roadmap"], provider_ref="txn_sweep")
    await fulfilment.fulfil(r.entitlement_id)

    assert (await delivery.sweep())["missing"] == 0

    Path((await db.fetchval("SELECT storage_uri FROM artifacts WHERE id=$1",
                            solution["artifacts"]["roadmap"])).replace("file://", "")).unlink()
    out = await delivery.sweep()
    assert out["missing"] == 1
    assert await db.fetchval(
        "SELECT missing_since FROM artifacts WHERE id=$1",
        solution["artifacts"]["roadmap"]) is not None


# ===========================================================================
# ATTRIBUTION — G6
# ===========================================================================

async def test_attribution_closes_the_loop(stub, solution):
    """Which source type actually produced revenue. Until an order exists there
    is nothing to attribute — which is why Pimlico could never close this."""
    L = await _ladder(stub, solution)
    r = await _buy(stub, L["instructions"], provider_ref="txn_attr")
    await orders.record_attribution(
        r.order_id, need_id=solution["need_id"], solution_id=solution["solution_id"],
        source_type="community", channel="sales_page")

    row = await db.fetchrow("SELECT * FROM attributions WHERE order_id=$1", r.order_id)
    assert row["source_type"] == "community"

    revenue = await db.fetchval(
        "SELECT sum(o.amount_minor) FROM orders o JOIN attributions a ON a.order_id=o.id "
        "WHERE a.source_type = 'community'")
    assert int(revenue) == L["instructions"].price_minor


# ===========================================================================
# THE PUBLISH GATE — a withheld deliverable cannot be sold
# ===========================================================================
# 🔴 Found 2026-08-09, in the money path, unexercised by any test.
#
# `forge reverify` sets artifacts.offerable = FALSE when a deliverable fails
# its structural or factual pass, and prints "withheld". But `publish` read
# only the `offers` table, so the withholding was advisory: publish the offer
# and the failed artifact was on sale. The verdict lived on the side that does
# not take money and was absent from the side that does.
#
# These tests are the reason the gate cannot be quietly removed again.

async def _unverified(solution_id: int, tier: str, **flags):
    sets = ", ".join(f"{k} = {v}" for k, v in
                     ({"offerable": "FALSE"} | flags).items())
    await db.execute(
        f"UPDATE artifacts SET {sets} WHERE solution_id = $1 AND tier = $2",
        solution_id, tier)


@pytest.mark.parametrize("tier", ["roadmap", "instructions", "deployed"])
async def test_a_withheld_artifact_on_ANY_rung_refuses_the_whole_ladder(
        stub, solution, tier):
    """Not just the failing rung — the ladder publishes whole or not at all."""
    await offers.create_ladder(solution["solution_id"], base_minor=BASE_MINOR,
                               provider="stub", store_id="jpd_test_store")
    await _unverified(solution["solution_id"], tier, factual_ok="FALSE")

    with pytest.raises(providers.ProviderError, match="deliverable that passed"):
        await offers.publish(solution["solution_id"])

    live = await db.fetchval(
        "SELECT count(*) FROM offers WHERE solution_id = $1 AND live",
        solution["solution_id"])
    assert live == 0, "a refused publish must leave NOTHING live"


async def test_the_refusal_names_the_tier_and_why_it_failed(stub, solution):
    await offers.create_ladder(solution["solution_id"], base_minor=BASE_MINOR,
                               provider="stub", store_id="jpd_test_store")
    await _unverified(solution["solution_id"], "instructions",
                      structural_ok="FALSE", factual_ok="FALSE")
    with pytest.raises(providers.ProviderError) as e:
        await offers.publish(solution["solution_id"])
    msg = str(e.value)
    assert "instructions" in msg
    assert "structural and factual" in msg
    assert f"reverify {solution['need_id']}" in msg, "must say how to fix it"


async def test_NEVER_verified_is_not_reported_as_FAILED(stub, solution):
    """NULL means nobody looked; FALSE means it was looked at and was wrong.
    Reporting the first as the second sends the operator hunting a defect that
    does not exist."""
    await offers.create_ladder(solution["solution_id"], base_minor=BASE_MINOR,
                               provider="stub", store_id="jpd_test_store")
    await _unverified(solution["solution_id"], "roadmap",
                      structural_ok="NULL", factual_ok="NULL")
    with pytest.raises(providers.ProviderError) as e:
        await offers.publish(solution["solution_id"])
    assert "has not been verified" in str(e.value)
    assert "failed" not in str(e.value)


async def test_a_missing_artifact_refuses_the_publish_not_just_the_fulfilment(
        stub, solution):
    """Previously this was caught only at fulfilment — AFTER the buyer paid."""
    await offers.create_ladder(solution["solution_id"], base_minor=BASE_MINOR,
                               provider="stub", store_id="jpd_test_store")
    await db.execute("DELETE FROM artifacts WHERE solution_id = $1 AND tier = 'deployed'",
                     solution["solution_id"])
    with pytest.raises(providers.ProviderError, match="no artifact has been built"):
        await offers.publish(solution["solution_id"])


async def test_the_gate_checks_the_SAME_row_fulfilment_would_deliver(stub, solution,
                                                                    tmp_path):
    """🔴 The subtle way to build a gate that does not gate.

    `fulfilment._artifact_for` takes the NEWEST artifact for (solution, tier).
    A gate that looked at any other row — the oldest, or one matched by need_id
    — would approve a verified sibling while the buyer received the newest,
    unverified one. Here a fresh failing artifact is added ALONGSIDE the
    passing one; publish must refuse.
    """
    f = tmp_path / "newer.pdf"
    f.write_bytes(b"%PDF-1.4 newer but unverified\n" * 40)
    await db.execute(
        "INSERT INTO artifacts (solution_id, need_id, tier, kind, sha256, bytes,"
        "                       storage_uri, structural_ok, factual_ok, offerable) "
        "VALUES ($1,$2,'deployed','pdf',$3,$4,$5,TRUE,FALSE,FALSE)",
        solution["solution_id"], solution["need_id"],
        hashlib.sha256(f.read_bytes()).hexdigest(), f.stat().st_size, f"file://{f}")

    await offers.create_ladder(solution["solution_id"], base_minor=BASE_MINOR,
                               provider="stub", store_id="jpd_test_store")
    with pytest.raises(providers.ProviderError, match="failed its factual check"):
        await offers.publish(solution["solution_id"])


async def test_a_fully_verified_ladder_still_publishes(stub, solution):
    """The gate must not be a wall. Everything green — all three go live."""
    await offers.create_ladder(solution["solution_id"], base_minor=BASE_MINOR,
                               provider="stub", store_id="jpd_test_store")
    assert await offers.publish(solution["solution_id"]) == 3
    live = await db.fetchval(
        "SELECT count(*) FROM offers WHERE solution_id = $1 AND live",
        solution["solution_id"])
    assert live == 3


async def test_republishing_after_a_fix_works(stub, solution):
    """Refusal is not terminal — fix the artifact and publish succeeds."""
    await offers.create_ladder(solution["solution_id"], base_minor=BASE_MINOR,
                               provider="stub", store_id="jpd_test_store")
    await _unverified(solution["solution_id"], "roadmap", factual_ok="FALSE")
    with pytest.raises(providers.ProviderError):
        await offers.publish(solution["solution_id"])
    await db.execute(
        "UPDATE artifacts SET factual_ok = TRUE, offerable = TRUE "
        "WHERE solution_id = $1 AND tier = 'roadmap'", solution["solution_id"])
    assert await offers.publish(solution["solution_id"]) == 3
