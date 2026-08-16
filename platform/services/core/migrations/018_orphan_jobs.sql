-- ============================================================================
-- JPD 018 — retire the driverless job row
--
-- integrity.manifest_check was seeded in 002 and NO code anywhere drives it
-- or stamps it, so it sat overdue forever — the "invisible limbo" C3 exists
-- to abolish, this time in the job registry rather than a connector.
--
-- Operator decision 2026-08-16: disable it. Its intended ground is already
-- covered — commerce.artifact_sweep re-verifies every artifact behind a live
-- token hourly (delivery.sweep), and each artifact's sha256 is checked at
-- mint time. If a distinct manifest check is ever wanted, implement it in
-- scheduler DISPATCH first and re-enable in the same change — a registry row
-- with no driver is a promise nobody is keeping.
--
-- commerce.notification_retry, the other driverless row found the same day,
-- got the opposite treatment: a driver (notify.retry_owed, dispatched by the
-- scheduler tick) — buyers who paid and were not told are a real obligation.
--
-- A migration rather than an UPDATE, so the test database rebuilt from
-- migrations agrees with production (the lesson from the calibrated
-- threshold: tuned state that lives only in production splits behaviour).
-- ============================================================================

UPDATE job_registry SET enabled = FALSE
 WHERE job_name = 'integrity.manifest_check';
