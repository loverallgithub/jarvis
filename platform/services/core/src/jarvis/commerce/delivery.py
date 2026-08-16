"""Download tokens and artifact integrity.

────────────────────────────────────────────────────────────────────────────
THE RULE THIS MODULE EXISTS TO ENFORCE
────────────────────────────────────────────────────────────────────────────
**A token is minted only after the artifact file is confirmed to exist.**

All three of Pimlico's existing delivery tokens point at files that do not
exist. They were minted from an *intention* to deliver rather than from the
*fact* of a deliverable, and nothing checked afterwards — so the failure would
surface as a buyer clicking a dead link, days later, with no alert anywhere.

Two mechanisms, because one is not enough:
  1. `mint()` stats the file first and refuses if it is absent or empty.
  2. `sweep()` re-checks every artifact behind a live token on a schedule,
     because a file that existed at mint time can vanish afterwards.

Tokens are stored as **sha256, never plaintext**. A download token is a bearer
credential — whoever holds it gets the product. Storing it in the clear makes a
database dump, a log line, or a support screenshot into free inventory.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import structlog

from .. import db
from ..config import settings

log = structlog.get_logger("commerce.delivery")

DEFAULT_TTL_DAYS = 365
DEFAULT_MAX_DOWNLOADS = 20


class ArtifactMissing(RuntimeError):
    """The artifact file is not on disk. Refuse to mint; this is not retryable
    by the buyer and must never be papered over with a receipt."""


class TokenInvalid(RuntimeError):
    """Presented token is unknown, expired, revoked, or exhausted."""


@dataclass(frozen=True)
class MintedToken:
    """The plaintext token is returned EXACTLY ONCE, here. It is never stored,
    never logged, and cannot be recovered — a lost token is re-minted."""
    token: str
    token_id: int
    expires_at: datetime
    artifact_id: int


def artifact_path(storage_uri: str) -> Path:
    """Resolve a storage URI to a local path.

    Only `file://` and bare paths are supported today. An unrecognised scheme
    raises rather than being coerced into a path that then "does not exist" —
    those two failures need different fixes and must not look alike.
    """
    if not storage_uri:
        raise ArtifactMissing("artifact has no storage_uri")
    parsed = urlparse(storage_uri)
    if parsed.scheme in ("", "file"):
        return Path(parsed.path if parsed.scheme == "file" else storage_uri)
    raise ArtifactMissing(
        f"unsupported storage scheme {parsed.scheme!r} — cannot verify existence")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def verify_artifact_present(artifact_id: int) -> Path:
    """Stat the file. Returns its path, or raises.

    Emptiness counts as missing: a zero-byte PDF is not a product, and it is
    exactly what a truncated write leaves behind.
    """
    row = await db.fetchrow(
        "SELECT id, storage_uri, bytes, sha256 FROM artifacts WHERE id = $1", artifact_id)
    if row is None:
        raise ArtifactMissing(f"artifact {artifact_id} does not exist")

    p = artifact_path(row["storage_uri"])
    if not p.is_file():
        await db.execute(
            "UPDATE artifacts SET missing_since = coalesce(missing_since, now()) WHERE id = $1",
            artifact_id)
        raise ArtifactMissing(f"artifact {artifact_id}: no file at {p}")
    if p.stat().st_size == 0:
        await db.execute(
            "UPDATE artifacts SET missing_since = coalesce(missing_since, now()) WHERE id = $1",
            artifact_id)
        raise ArtifactMissing(f"artifact {artifact_id}: file at {p} is empty")

    await db.execute(
        "UPDATE artifacts SET verified_present_at = now(), missing_since = NULL WHERE id = $1",
        artifact_id)
    return p


async def mint(entitlement_id: int, artifact_id: int, *,
               ttl_days: int = DEFAULT_TTL_DAYS,
               max_downloads: int = DEFAULT_MAX_DOWNLOADS) -> MintedToken:
    """Mint a download token — AFTER proving the artifact is really there."""
    await verify_artifact_present(artifact_id)          # raises if absent

    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=ttl_days)
    tid = await db.fetchval(
        """
        INSERT INTO delivery_tokens (entitlement_id, artifact_id, token_hash,
                                     expires_at, max_downloads)
        VALUES ($1, $2, $3, $4, $5) RETURNING id
        """,
        entitlement_id, artifact_id, _hash(token), expires, max_downloads)

    log.info("delivery.token_minted", entitlement_id=entitlement_id,
             artifact_id=artifact_id, token_id=tid)   # never the token itself
    return MintedToken(token=token, token_id=int(tid),
                       expires_at=expires, artifact_id=artifact_id)


async def redeem(token: str) -> tuple[Path, dict]:
    """Validate a presented token and return the file to serve.

    The download counter is incremented in the same guarded UPDATE that checks
    the limit, so two concurrent requests cannot both pass a check-then-write.
    """
    row = await db.fetchrow(
        """
        UPDATE delivery_tokens
           SET download_count = download_count + 1, last_download_at = now()
         WHERE token_hash = $1
           AND revoked_at IS NULL
           AND expires_at > now()
           AND download_count < max_downloads
        RETURNING id, entitlement_id, artifact_id, download_count, max_downloads
        """,
        _hash(token))

    if row is None:
        # Distinguish "never existed" from "no longer usable" for the log only.
        # The CALLER gets one undifferentiated failure: telling an attacker
        # which of their guesses was once a real token is a gift.
        known = await db.fetchrow(
            "SELECT expires_at, revoked_at, download_count, max_downloads "
            "FROM delivery_tokens WHERE token_hash = $1", _hash(token))
        reason = "unknown_token" if known is None else (
            "revoked" if known["revoked_at"] else
            "expired" if known["expires_at"] <= datetime.now(timezone.utc) else
            "download_limit_reached")
        log.warning("delivery.token_rejected", reason=reason)
        raise TokenInvalid("token is not valid")

    # Existence is re-checked at every redemption, not just at mint. A file can
    # vanish between the two, and the buyer must not meet a stack trace.
    path = await verify_artifact_present(row["artifact_id"])
    log.info("delivery.token_redeemed", token_id=row["id"],
             count=row["download_count"], limit=row["max_downloads"])
    return path, dict(row)


async def revoke(token_id: int, reason: str = "") -> bool:
    ok = await db.fetchval(
        "UPDATE delivery_tokens SET revoked_at = now() WHERE id = $1 "
        "AND revoked_at IS NULL RETURNING id", token_id)
    if ok:
        log.info("delivery.token_revoked", token_id=token_id, reason=reason[:200])
    return ok is not None


async def sweep() -> dict[str, int]:
    """Periodic integrity check over every artifact behind a live token.

    This is the half that catches what mint-time checking cannot: a file
    deleted, a volume remounted, a cleanup job that was too enthusiastic. A
    buyer must never be the monitoring system.
    """
    rows = await db.fetch(
        """
        SELECT DISTINCT a.id
          FROM artifacts a
          JOIN delivery_tokens t ON t.artifact_id = a.id
         WHERE t.revoked_at IS NULL AND t.expires_at > now()
        """)
    checked = present = missing = 0
    for r in rows:
        checked += 1
        try:
            await verify_artifact_present(r["id"])
            present += 1
        except ArtifactMissing as e:
            missing += 1
            log.error("delivery.artifact_missing", artifact_id=r["id"], detail=str(e))

    if missing:
        log.error("delivery.sweep_found_missing", missing=missing, checked=checked)
    await db.execute(
        "UPDATE job_registry SET last_success_at = now() WHERE job_name = $1",
        "commerce.artifact_sweep")
    return {"checked": checked, "present": present, "missing": missing}
