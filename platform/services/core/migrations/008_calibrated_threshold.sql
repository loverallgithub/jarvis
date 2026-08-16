-- ============================================================================
-- JPD 008 — persist the CALIBRATED clustering threshold
--
-- 🔴 Migration 007 seeded 0.18. Calibration then measured 0.42 as correct, and
-- that value was applied with an UPDATE **against the production database
-- only**. The test database — which is rebuilt from the migrations every run —
-- still had 0.18, so the funnel behaved differently in tests than in
-- production and a census test failed for a reason that had nothing to do with
-- its subject.
--
-- Caught by a test. It is the same class as every other config-drift bug in
-- this platform: a value tuned in one place and not written down in the place
-- that recreates the system.
--
-- MEASUREMENT (68 real signals, overlap-coefficient similarity):
--   0.60  6 clusters,  0 cross-source
--   0.50  6 clusters,  1 cross-source, severity 4.5
--   0.42  7 clusters,  2 cross-source, severity 4.5, largest 9   <-- chosen
--   0.36  6 clusters,  2 cross-source, largest 19 (over-merging)
--   0.30  7 clusters,  3 cross-source, largest 20 (over-merged, unlabelled)
-- ============================================================================

UPDATE discovery_params
   SET value = 0.42,
       rationale = 'Calibrated on 68 real signals with the overlap coefficient. '
                   '0.42 yields a 3-source-type cluster (community+review+search) at '
                   'severity 4.5 without over-merging; at 0.36 and below clusters '
                   'collapse into unlabelled 19-52 member blobs; at 0.60 nothing '
                   'spans sources. 0.18 and 0.28 were unmeasured guesses.',
       updated_at = now()
 WHERE param = 'similarity_threshold';
