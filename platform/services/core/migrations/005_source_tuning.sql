-- ============================================================================
-- JPD 005 — source tuning as DATA, and the discovery funnel's tables
--
-- Written after LOOKING at the first 155 real signals. That inspection is only
-- possible because signals are stored, and it found three problems that would
-- have made the funnel silently useless:
--
--   1. every `sec_edgar` concept was "E X - 9 9 . 1 risk language" — a
--      `' '.join()` over a STRING, splitting it into characters
--   2. `app_store_reviews` was pointed at WhatsApp and Starbucks, labelled
--      "Slack-ish" and "Numbers" in a comment nobody checked. The design says
--      B2B tooling; it produced Spanish consumer complaints about ads
--   3. `hacker_news` and `google_suggest` returned real but consumer-grade
--      material ("best software for 3d printing")
--
-- This is exactly the diagnosis Pimlico could never make: its funnel returned
-- nothing for three weeks and there was no census and no stored signal to look
-- at. Tuning now lives in `sources.config` so it is an UPDATE, not a redeploy.
-- ============================================================================

-- Pain-shaped, B2B-shaped queries. Bare topic words return noise; phrases in
-- which somebody is DESCRIBING A PROBLEM return what the funnel is looking for.
UPDATE sources SET config = config || jsonb_build_object('queries', jsonb_build_array(
    '"spend hours every week"',
    '"we do this manually"',
    '"there is no good tool"',
    '"biggest bottleneck"',
    '"costs us a fortune"',
    '"nightmare to reconcile"',
    '"still using spreadsheets"'
)) WHERE name = 'hacker_news';

-- Verified through the iTunes lookup API, every one:
--   618783545 Slack · 546505307 Zoom Workplace · 489969512 Asana
--   461504587 Trello · 1232780281 Notion · 1298450641 monday.com
--   584606479 Intuit QuickBooks
UPDATE sources SET config = config || jsonb_build_object('apps', jsonb_build_array(
    '618783545', '546505307', '489969512', '461504587',
    '1232780281', '1298450641', '584606479'
)) WHERE name = 'app_store_reviews';

-- B2B intent, not consumer. "best software for 3d printing" is a real query and
-- a useless signal for this platform.
UPDATE sources SET config = config || jsonb_build_object('seeds', jsonb_build_array(
    'software to automate invoice',
    'tool to track subcontractor',
    'how to stop losing revenue from',
    'best way to reconcile',
    'software for small construction',
    'automate compliance reporting'
)) WHERE name = 'google_suggest';

-- ---------------------------------------------------------------------------
-- DISCOVERY FUNNEL
-- ---------------------------------------------------------------------------

-- Clusters gain the fields the funnel actually reads.
ALTER TABLE clusters ADD COLUMN IF NOT EXISTS method TEXT NOT NULL DEFAULT 'lexical';
ALTER TABLE clusters ADD COLUMN IF NOT EXISTS terms JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE clusters ADD COLUMN IF NOT EXISTS source_types JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE clusters ADD COLUMN IF NOT EXISTS distinct_voices INT NOT NULL DEFAULT 0;
ALTER TABLE clusters ADD COLUMN IF NOT EXISTS run_id BIGINT;

-- Needs gain the sub-scores. `gap` stays NULL until Phase B produces
-- competitive data — Pimlico weighted `gap` at 0.25, its second-highest weight,
-- with no competitive data at all.
ALTER TABLE needs ADD COLUMN IF NOT EXISTS frequency NUMERIC(6,2);
ALTER TABLE needs ADD COLUMN IF NOT EXISTS severity NUMERIC(4,2);
ALTER TABLE needs ADD COLUMN IF NOT EXISTS cross_source INT;
ALTER TABLE needs ADD COLUMN IF NOT EXISTS commercial_intent INT;
ALTER TABLE needs ADD COLUMN IF NOT EXISTS distinct_voices INT;
ALTER TABLE needs ADD COLUMN IF NOT EXISTS qualification JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE needs ADD COLUMN IF NOT EXISTS run_id BIGINT;

-- Scoring weights are ROWS. Retuning the score is an UPDATE.
CREATE TABLE IF NOT EXISTS score_weights (
    component  TEXT PRIMARY KEY,
    weight     NUMERIC(4,3) NOT NULL CHECK (weight >= 0),
    rationale  TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO score_weights (component, weight, rationale) VALUES
    ('frequency',         0.25, 'Independent mentions, counted over DISTINCT VOICES not raw rows.'),
    ('severity',          0.30, 'Averaged over pain evidence only. The strongest single predictor of willingness to pay.'),
    ('cross_source',      0.25, 'Distinct source types. authority cannot self-corroborate.'),
    ('commercial_intent', 0.20, 'Evidence of spend, tooling or hiring. Pain without budget is not a product.')
ON CONFLICT (component) DO NOTHING;

-- gate_evaluations already exists (001). Index for counterfactual replay:
-- "what would have promoted at severity >= 3.5" must be a fast query, or
-- nobody will run it.
CREATE INDEX IF NOT EXISTS gate_eval_replay_idx
    ON gate_evaluations (gate, value, evaluated_at DESC);

INSERT INTO job_registry (job_name, expected_interval_s) VALUES
    ('discovery.funnel', 86400)
ON CONFLICT (job_name) DO NOTHING;
