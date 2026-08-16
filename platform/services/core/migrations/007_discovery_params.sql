-- ============================================================================
-- JPD 007 — clustering parameters as DATA, calibrated against real signals
--
-- The similarity threshold was 0.28, which was a GUESS. Measured against 68
-- real admissible signals, with stemming enabled:
--
--   thresh  clusters  largest  cross-source
--    0.30       5        5         0
--    0.25       6        5         0
--    0.20       6        6         2    <- cross-source corroboration appears
--    0.16       7        6         2
--    0.12       7       16         1    <- over-merges into one 16-member blob
--
-- 0.18 sits in the band where clusters span source types without collapsing
-- into a single undifferentiated group. Recorded here so the next person sees
-- the measurement rather than the number.
-- ============================================================================

CREATE TABLE IF NOT EXISTS discovery_params (
    param      TEXT PRIMARY KEY,
    value      NUMERIC(10,4) NOT NULL,
    rationale  TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO discovery_params (param, value, rationale) VALUES
    ('similarity_threshold', 0.18,
     'Calibrated on 68 real signals: cross-source clusters appear at <=0.20 and over-merge at 0.12. 0.28 was an unmeasured guess.'),
    ('window_days', 30,
     'Rolling admission window.'),
    ('min_cluster_size', 2,
     'A cluster of one corroborates nothing.')
ON CONFLICT (param) DO NOTHING;
