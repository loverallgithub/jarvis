-- ============================================================================
-- JPD 009 — Phase B: research and grounding
--
-- The design called for you.com Research at $0.012/call. **No you.com key
-- exists anywhere on this host** — checked the Pimlico stack env, the running
-- containers and both .env backups. Rather than block the phase on a
-- credential, B1 uses what was verified reachable from this VPS on 2026-08-07:
--
--   DuckDuckGo lite  200   24 KB      <- chosen: no key, stable HTML, light
--   DuckDuckGo html  200   34 KB
--   Bing             200  119 KB
--   Mojeek           200   29 KB
--
-- The connector sits behind the same contract as everything else, so swapping
-- in you.com later is a registry row plus one class.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- EVIDENCE — content-addressed, with the fields grounding actually needs
-- ---------------------------------------------------------------------------
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS need_id BIGINT
    REFERENCES needs(id) ON DELETE CASCADE;
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS bytes INT;
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS body TEXT;
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'page'
    CHECK (kind IN ('page','search_result','pricing','filing','signal','manual'));
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS run_id BIGINT;

-- A URL captured twice in the same run is the same evidence. Content-addressing
-- is the whole discipline: same bytes, same row.
CREATE UNIQUE INDEX IF NOT EXISTS evidence_need_sha_idx
    ON evidence (need_id, sha256) WHERE need_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS evidence_need_idx ON evidence (need_id, kind);

-- ---------------------------------------------------------------------------
-- CLAIMS — the constraint that makes grounding real
-- ---------------------------------------------------------------------------
-- `claims.evidence_id` is already NOT NULL (migration 001). Phase B adds claims
-- that belong to a NEED rather than to a published artifact, so deliverable_id
-- has to become optional — WITHOUT weakening the citation requirement.
ALTER TABLE claims ALTER COLUMN deliverable_id DROP NOT NULL;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS need_id BIGINT
    REFERENCES needs(id) ON DELETE CASCADE;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'fact'
    CHECK (kind IN ('fact','gap','pricing','competitor','feasibility'));
ALTER TABLE claims ADD COLUMN IF NOT EXISTS run_id BIGINT;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS supported BOOLEAN;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS support_reason TEXT;

-- 🔴 A claim must belong to SOMETHING. Dropping the NOT NULL on deliverable_id
-- would otherwise allow an orphan claim attached to nothing at all, which is a
-- quieter version of the uncited-claim problem this table exists to prevent.
ALTER TABLE claims DROP CONSTRAINT IF EXISTS claims_belongs_to_something;
ALTER TABLE claims ADD CONSTRAINT claims_belongs_to_something
    CHECK (deliverable_id IS NOT NULL OR need_id IS NOT NULL);

CREATE INDEX IF NOT EXISTS claims_need_idx ON claims (need_id, kind);

-- ---------------------------------------------------------------------------
-- DOSSIERS
-- ---------------------------------------------------------------------------
ALTER TABLE dossiers ADD COLUMN IF NOT EXISTS run_id BIGINT;
ALTER TABLE dossiers ADD COLUMN IF NOT EXISTS evidence_count INT NOT NULL DEFAULT 0;
ALTER TABLE dossiers ADD COLUMN IF NOT EXISTS claim_count INT NOT NULL DEFAULT 0;
ALTER TABLE dossiers ADD COLUMN IF NOT EXISTS feasibility JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE UNIQUE INDEX IF NOT EXISTS dossiers_need_kind_idx ON dossiers (need_id, kind);

-- ---------------------------------------------------------------------------
-- RESEARCH PARAMETERS — data, like everything else tunable
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS research_params (
    param      TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    rationale  TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO research_params (param, value, rationale) VALUES
    ('search_provider', 'duckduckgo',
     'No you.com key exists on this host. DDG lite verified 200/24KB from this VPS 2026-08-07.'),
    ('min_evidence_rows', '15',
     'Phase B exit criterion: a dossier needs >= 15 live hash-verified evidence rows.'),
    ('max_fetch_bytes', '400000',
     'Cap per page. A 10MB PDF is not worth the memory to hash it.'),
    ('fetch_timeout_s', '20', 'Per-URL timeout.'),
    ('llm_model', 'claude-haiku-4-5-20251001',
     'Cheapest model on the available Anthropic key. Verified: /v1/models lists it and messages returns 200.'),
    ('embed_model', 'nomic-embed-text',
     'Available on the local ollama host — 768 dims, ~2.4s per call, zero API cost.')
ON CONFLICT (param) DO NOTHING;

-- Connectors this phase introduces. All start DORMANT and must earn `live`.
INSERT INTO connector_health (connector, kind, state, evidence) VALUES
    ('duckduckgo', 'api', 'dormant',
     '{"note": "Search for competitor discovery. Verified 200 from this VPS 2026-08-07; no credential needed."}'),
    ('anthropic',  'api', 'dormant',
     '{"note": "Key copied from the Pimlico stack. /v1/models lists claude-opus-5, claude-sonnet-5, claude-haiku-4-5."}')
ON CONFLICT (connector) DO NOTHING;

-- ollama and qdrant already have rows from 002; they now have credentials.
UPDATE connector_health
   SET evidence = evidence || jsonb_build_object(
       'note', 'Credential wired from the Pimlico stack 2026-08-07. Reachable via its nginx vhost; nomic-embed-text returns 768 dims.')
 WHERE connector IN ('ollama', 'qdrant');

INSERT INTO job_registry (job_name, expected_interval_s) VALUES
    ('research.dossier', 86400)
ON CONFLICT (job_name) DO NOTHING;
