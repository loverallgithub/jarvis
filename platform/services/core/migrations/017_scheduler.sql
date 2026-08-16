-- ============================================================================
-- JPD 017 — scheduler attempt tracking
--
-- last_success_at is stamped only when a job WORKED, so a failing daily job
-- would be retried by every 15-minute scheduler tick — a money-burn loop for
-- the spending jobs (research.dossier, forge.build). last_attempt_at is
-- stamped on every try and gives the tick its cooldown.
-- ============================================================================

ALTER TABLE job_registry ADD COLUMN IF NOT EXISTS last_attempt_at timestamptz;
