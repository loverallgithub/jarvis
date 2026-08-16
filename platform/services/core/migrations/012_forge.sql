-- ============================================================================
-- JPD 012 — the forge
--
-- The three tiers are the pipeline's OWN ARTIFACTS, not three separate
-- products: Roadmap (C) → Instructions (D, a superset) → Deployed (E, a
-- superset of that). Three price points at zero marginal cost, a natural
-- upgrade ladder, and — critically — if the build fails at Deployed, two
-- complete products still exist and still sell.
--
-- Pimlico's all-or-nothing model turned every build failure into zero revenue.
-- ============================================================================

ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS need_id BIGINT
    REFERENCES needs(id) ON DELETE CASCADE;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS sections INT NOT NULL DEFAULT 0;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS words INT NOT NULL DEFAULT 0;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS run_id BIGINT;
-- Verification is recorded ON the artifact, so "was this checked?" is a column
-- rather than a memory.
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS structural_ok BOOLEAN;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS factual_ok BOOLEAN;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS verify_detail JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS offerable BOOLEAN NOT NULL DEFAULT FALSE;

-- One current artifact per (need, tier). Regenerating replaces.
CREATE UNIQUE INDEX IF NOT EXISTS artifacts_need_tier_idx
    ON artifacts (need_id, tier) WHERE need_id IS NOT NULL;

-- acceptance_tests exists (001) but was keyed to solution_id only.
ALTER TABLE acceptance_tests ADD COLUMN IF NOT EXISTS need_id BIGINT
    REFERENCES needs(id) ON DELETE CASCADE;
ALTER TABLE acceptance_tests ALTER COLUMN solution_id DROP NOT NULL;
ALTER TABLE acceptance_tests ADD CONSTRAINT acceptance_belongs_to_something
    CHECK (solution_id IS NOT NULL OR need_id IS NOT NULL);

INSERT INTO research_params (param, value, rationale) VALUES
    ('forge_model', 'claude-opus-5',
     'Section generation. One LLM call PER SECTION, never one per product — that is what produced Pimlico''s genuine 24-28k-word depth and it is the right call.'),
    ('forge_max_sections', '8',
     'Size cap. ⚠️ It must ALSO truncate the PLAN: Pimlico capped output while keeping the full plan, so verify then failed on "fewer sections than planned" and the run was guaranteed to fail after paying for the work.'),
    ('forge_min_words_per_section', '120',
     'Below this a section is a heading with an apology under it.')
ON CONFLICT (param) DO UPDATE SET value = EXCLUDED.value, rationale = EXCLUDED.rationale;

INSERT INTO job_registry (job_name, expected_interval_s) VALUES
    ('forge.build', 86400)
ON CONFLICT (job_name) DO NOTHING;
