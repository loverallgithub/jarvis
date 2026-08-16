-- ============================================================================
-- JPD 014 — a claim can be cited by MORE THAN ONE artifact
--
-- 🔴 `claims.deliverable_id` is single-valued, so packaging three tiers made
-- each one STEAL the citations from the last. Observed on need 13:
--
--   roadmap       claims_checked = 0   -> factual_ok = TRUE (vacuously)
--   instructions  claims_checked = 0   -> factual_ok = TRUE (vacuously)
--   deployed      claims_checked = 14  -> factual_ok = FALSE
--
-- Two artifacts were marked OFFERABLE because their citations had been taken
-- away from them, not because they were verified. A verifier that passes work
-- it never checked is worse than no verifier.
--
-- The tiers are supersets of each other, so the SAME claim is legitimately
-- cited by all three. That is a many-to-many relationship and it needs a
-- many-to-many table.
-- ============================================================================

CREATE TABLE IF NOT EXISTS artifact_claims (
    artifact_id BIGINT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    claim_id    BIGINT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (artifact_id, claim_id)
);

CREATE INDEX IF NOT EXISTS artifact_claims_claim_idx ON artifact_claims (claim_id);

-- Carry across whatever the single-valued column currently holds.
INSERT INTO artifact_claims (artifact_id, claim_id)
SELECT deliverable_id, id FROM claims WHERE deliverable_id IS NOT NULL
ON CONFLICT DO NOTHING;
