"""G3 — deliver exactly what was bought.

Two rules that look small and are not:

**Deliver the tier's whole ladder, not just its top rung.** Each tier is a
superset of the one below: Instructions = Roadmap + build manual. An
Instructions buyer who receives only the manual has been shortchanged by the
data model, silently.

**Deliver ONLY the delta on an upgrade.** Re-sending everything on a tier
upgrade is not merely wasteful — it puts our idempotency bugs in the buyer's
inbox, which is the last place anyone wants to discover them.

Fulfilment is idempotent per (entitlement, tier). Running it twice delivers
nothing twice; that property is what makes it safe to retry after a partial
failure, which is the only realistic recovery path when three artifacts are
delivered and the fourth is missing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import structlog

from .. import db
from . import delivery
from .delivery import ArtifactMissing
from .pricing import delta_tiers, tiers_covered

log = structlog.get_logger("commerce.fulfilment")


@dataclass
class FulfilmentResult:
    entitlement_id: int
    delivered: list[dict] = field(default_factory=list)
    already_delivered: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Partial delivery is NOT success.

        A buyer who received two of three artifacts has a broken purchase, and
        reporting that as fulfilled is how it stays broken.
        """
        return not self.failed

    @property
    def status(self) -> str:
        if self.failed and self.delivered:
            return "partial"
        if self.failed:
            return "failed"
        return "delivered"


async def _artifact_for(solution_id: int, tier: str) -> Optional[dict]:
    """Newest artifact for this solution and tier."""
    row = await db.fetchrow(
        "SELECT id, tier, kind, storage_uri, bytes, sha256 FROM artifacts "
        "WHERE solution_id = $1 AND tier = $2 ORDER BY id DESC LIMIT 1",
        solution_id, tier)
    return dict(row) if row else None


async def fulfil(entitlement_id: int, *, only_tiers: Optional[list[str]] = None,
                 is_delta: bool = False) -> FulfilmentResult:
    ent = await db.fetchrow(
        "SELECT id, order_id, buyer_ref, solution_id, tier, revoked_at "
        "FROM entitlements WHERE id = $1", entitlement_id)
    if ent is None:
        raise LookupError(f"entitlement {entitlement_id} does not exist")
    if ent["revoked_at"] is not None:
        raise RuntimeError(
            f"entitlement {entitlement_id} was revoked at {ent['revoked_at']} — "
            f"refusing to deliver against it")

    wanted = only_tiers if only_tiers is not None else tiers_covered(ent["tier"])
    result = FulfilmentResult(entitlement_id=entitlement_id)

    for tier in wanted:
        # Idempotency per (entitlement, tier): a retry after a partial failure
        # must not re-deliver what already went out.
        existing = await db.fetchrow(
            "SELECT id, status FROM fulfilments WHERE entitlement_id = $1 AND tier = $2 "
            "AND status = 'delivered'", entitlement_id, tier)
        if existing:
            result.already_delivered.append(tier)
            continue

        artifact = await _artifact_for(ent["solution_id"], tier)
        if artifact is None:
            reason = f"no artifact exists for solution {ent['solution_id']} tier {tier}"
            await _record_failure(entitlement_id, tier, reason, is_delta)
            result.failed.append({"tier": tier, "reason": reason})
            log.error("fulfilment.no_artifact", entitlement_id=entitlement_id, tier=tier)
            continue

        try:
            # mint() verifies the file is really on disk before issuing a token.
            token = await delivery.mint(entitlement_id, artifact["id"])
        except ArtifactMissing as e:
            reason = str(e)
            await _record_failure(entitlement_id, tier, reason, is_delta)
            result.failed.append({"tier": tier, "reason": reason})
            log.error("fulfilment.artifact_missing", entitlement_id=entitlement_id,
                      tier=tier, detail=reason)
            continue

        fid = await db.fetchval(
            """
            INSERT INTO fulfilments (entitlement_id, status, artifact_id, delivered_at,
                                     channel, tier, is_delta, evidence)
            VALUES ($1,'delivered',$2,now(),'download',$3,$4,$5::jsonb)
            RETURNING id
            """,
            entitlement_id, artifact["id"], tier, is_delta,
            _evidence(token.token_id, artifact))

        result.delivered.append({
            "tier": tier, "fulfilment_id": int(fid), "artifact_id": artifact["id"],
            # The plaintext token exists only in this return value. It is handed
            # to the notifier and never persisted or logged.
            "token": token.token, "expires_at": token.expires_at,
        })
        log.info("fulfilment.delivered", entitlement_id=entitlement_id, tier=tier,
                 artifact_id=artifact["id"], fulfilment_id=int(fid))

    if result.failed:
        log.error("fulfilment.incomplete", entitlement_id=entitlement_id,
                  delivered=len(result.delivered), failed=len(result.failed))
    return result


def _evidence(token_id: int, artifact: dict) -> str:
    import json
    return json.dumps({"token_id": token_id, "artifact_sha256": artifact["sha256"],
                       "bytes": artifact["bytes"]}, default=str)


async def _record_failure(entitlement_id: int, tier: str, reason: str,
                          is_delta: bool) -> None:
    """A failed delivery is a ROW, not just a log line.

    It has to be queryable: "which buyers paid and did not receive" is the
    single most important question this system can be asked, and it must not
    require grepping logs.
    """
    await db.execute(
        """
        INSERT INTO fulfilments (entitlement_id, status, tier, is_delta, error, attempt)
        VALUES ($1,'failed',$2,$3,$4,1)
        """,
        entitlement_id, tier, is_delta, reason[:500])


async def fulfil_upgrade(from_entitlement_id: int, to_entitlement_id: int) -> FulfilmentResult:
    """G5 — deliver only what the upgrade added."""
    rows = await db.fetch(
        "SELECT id, tier FROM entitlements WHERE id = ANY($1::bigint[])",
        [from_entitlement_id, to_entitlement_id])
    tiers = {r["id"]: r["tier"] for r in rows}
    if len(tiers) != 2:
        raise LookupError("both entitlements must exist")

    only = delta_tiers(tiers[from_entitlement_id], tiers[to_entitlement_id])
    log.info("fulfilment.upgrade_delta", frm=tiers[from_entitlement_id],
             to=tiers[to_entitlement_id], delivering=only)
    return await fulfil(to_entitlement_id, only_tiers=only, is_delta=True)


async def undelivered_paid_orders() -> list[dict]:
    """Buyers who paid and have not received everything they bought.

    The query that must never return rows, exposed so it can be alerted on
    rather than remembered.
    """
    rows = await db.fetch(
        """
        SELECT o.id AS order_id, o.buyer_ref, o.buyer_email, o.created_at,
               e.id AS entitlement_id, e.tier
          FROM orders o
          JOIN entitlements e ON e.order_id = o.id
         WHERE o.status IN ('verified','fulfilled')
           AND e.revoked_at IS NULL
           AND EXISTS (SELECT 1 FROM fulfilments f
                        WHERE f.entitlement_id = e.id AND f.status = 'failed')
         ORDER BY o.created_at
        """)
    return [dict(r) for r in rows]
