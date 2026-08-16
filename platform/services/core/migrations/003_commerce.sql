-- ============================================================================
-- JPD 003 — the money path
--
-- Phase 0 created offers/orders/entitlements/fulfilments. This migration adds
-- what actually taking money requires: hashed delivery tokens, a notification
-- ledger, tier ratios as DATA, a raw webhook audit trail, and attribution.
--
-- Governing fact: Pimlico has nine live products, working sales pages, working
-- checkout links — and has never processed a single order. Every table here is
-- shaped by a specific way that could go wrong quietly.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- PRICING POLICY — DEC-001, as data
-- ---------------------------------------------------------------------------
-- The RATIO is a business decision; the ANCHOR is researched per solution from
-- Phase B willingness-to-pay evidence. Storing ratios as rows means retuning
-- the ladder is an UPDATE, not a redeploy — same discipline as gate_thresholds.
CREATE TABLE IF NOT EXISTS pricing_policy (
    tier          TEXT PRIMARY KEY CHECK (tier IN ('roadmap','instructions','deployed')),
    ratio_min     NUMERIC(6,2) NOT NULL CHECK (ratio_min > 0),
    ratio_max     NUMERIC(6,2) NOT NULL CHECK (ratio_max > 0),
    rationale     TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (ratio_max >= ratio_min)
);

INSERT INTO pricing_policy (tier, ratio_min, ratio_max, rationale) VALUES
    ('roadmap',      1.0,  1.0,  'The anchor. Set per solution from observed willingness-to-pay (dossiers.kind=''pricing''), never from a regex over one page.'),
    ('instructions', 3.0,  4.0,  'Roadmap + full build manual, configs, credentials, acceptance tests. Buyer has a team.'),
    ('deployed',    10.0, 15.0,  'Instructions + built, configured, tested, handed over. Buyer wants the outcome, not the work.')
ON CONFLICT (tier) DO NOTHING;

-- ---------------------------------------------------------------------------
-- OFFERS — additions
-- ---------------------------------------------------------------------------
ALTER TABLE offers ADD COLUMN IF NOT EXISTS checkout_url TEXT;
ALTER TABLE offers ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT 'ghl';
-- The store this offer belongs to. DEC-002: JPD gets its own GHL store so that
-- "our products" is a reliable filter instead of a name-guess. The tenant is
-- co-tenanted with an unrelated business — 53 products, most of them not ours.
ALTER TABLE offers ADD COLUMN IF NOT EXISTS store_id TEXT;

-- ---------------------------------------------------------------------------
-- PROVIDER EVENTS — raw webhook audit
-- ---------------------------------------------------------------------------
-- Every inbound webhook is recorded BEFORE it is interpreted, including ones
-- rejected for a bad signature. When money is involved, "we saw nothing" and
-- "we rejected it" must be distinguishable after the fact.
CREATE TABLE IF NOT EXISTS provider_events (
    id              BIGSERIAL PRIMARY KEY,
    provider        TEXT NOT NULL,
    event_type      TEXT,
    provider_ref    TEXT,
    signature_valid BOOLEAN NOT NULL,
    signature_reason TEXT,
    accepted        BOOLEAN NOT NULL,
    reject_reason   TEXT,
    payload_raw     TEXT NOT NULL,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS provider_events_ref_idx ON provider_events (provider, provider_ref);
CREATE INDEX IF NOT EXISTS provider_events_time_idx ON provider_events (received_at DESC);

-- ---------------------------------------------------------------------------
-- DELIVERY TOKENS
-- ---------------------------------------------------------------------------
-- 🔴 token_hash, NOT the token. A download token is a bearer credential: anyone
-- holding it gets the artifact. Storing the plaintext means a DB dump, a log
-- line or a support screenshot is a free product. We store sha256 and compare.
--
-- 🔴 artifact_id is NOT NULL and the artifact's existence on disk is checked
-- BEFORE a token is minted. All three of Pimlico's existing delivery tokens
-- point at files that do not exist — the token was minted from an intention
-- rather than from a fact.
CREATE TABLE IF NOT EXISTS delivery_tokens (
    id              BIGSERIAL PRIMARY KEY,
    entitlement_id  BIGINT NOT NULL REFERENCES entitlements(id) ON DELETE CASCADE,
    artifact_id     BIGINT NOT NULL REFERENCES artifacts(id) ON DELETE RESTRICT,
    token_hash      TEXT NOT NULL UNIQUE,
    expires_at      TIMESTAMPTZ NOT NULL,
    max_downloads   INT NOT NULL DEFAULT 20 CHECK (max_downloads > 0),
    download_count  INT NOT NULL DEFAULT 0,
    last_download_at TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS delivery_tokens_ent_idx ON delivery_tokens (entitlement_id);

-- ---------------------------------------------------------------------------
-- NOTIFICATIONS
-- ---------------------------------------------------------------------------
-- G4: "every notification failure is a METRIC, not a log line." A buyer who
-- paid and never heard from us is the worst possible outcome, and it is exactly
-- the kind of failure that produces no error anywhere.
CREATE TABLE IF NOT EXISTS notifications (
    id           BIGSERIAL PRIMARY KEY,
    order_id     BIGINT REFERENCES orders(id) ON DELETE CASCADE,
    entitlement_id BIGINT REFERENCES entitlements(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,
    channel      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','sent','failed','skipped_dormant')),
    attempt      INT NOT NULL DEFAULT 0,
    error        TEXT,
    sent_at      TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS notifications_status_idx ON notifications (status, created_at);

-- ---------------------------------------------------------------------------
-- ATTRIBUTION — G6, closing the loop
-- ---------------------------------------------------------------------------
-- Which source type, which need, which channel produced this order. Until this
-- exists, gate thresholds are informed guesses; with it, calibration is a SQL
-- query over real revenue. Pimlico could never close this loop because it never
-- had an order to close it with.
CREATE TABLE IF NOT EXISTS attributions (
    id           BIGSERIAL PRIMARY KEY,
    order_id     BIGINT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    need_id      BIGINT REFERENCES needs(id) ON DELETE SET NULL,
    solution_id  BIGINT REFERENCES solutions(id) ON DELETE SET NULL,
    source_type  TEXT,
    channel      TEXT,
    voice_id     BIGINT REFERENCES voices(id) ON DELETE SET NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (order_id)
);

-- ---------------------------------------------------------------------------
-- ORDERS — additions
-- ---------------------------------------------------------------------------
-- Why an order was refused, kept on the order itself. A rejected order that
-- records no reason is indistinguishable from one nobody looked at.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS reject_reason TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb;

-- ---------------------------------------------------------------------------
-- FULFILMENTS — additions
-- ---------------------------------------------------------------------------
-- An upgrade delivers ONLY the delta. Recording which tier a fulfilment
-- actually covered is what makes that checkable rather than assumed.
ALTER TABLE fulfilments ADD COLUMN IF NOT EXISTS tier TEXT
    CHECK (tier IN ('roadmap','instructions','deployed'));
ALTER TABLE fulfilments ADD COLUMN IF NOT EXISTS is_delta BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE fulfilments ADD COLUMN IF NOT EXISTS attempt INT NOT NULL DEFAULT 0;
ALTER TABLE fulfilments ADD COLUMN IF NOT EXISTS error TEXT;

-- ---------------------------------------------------------------------------
-- ARTIFACTS — integrity at delivery time
-- ---------------------------------------------------------------------------
-- Set by the existence sweep. A token is never minted against an artifact whose
-- file is missing, and the sweep catches files that vanish AFTER minting.
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS verified_present_at TIMESTAMPTZ;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS missing_since TIMESTAMPTZ;

INSERT INTO job_registry (job_name, expected_interval_s) VALUES
    ('commerce.artifact_sweep', 3600),
    ('commerce.notification_retry', 900)
ON CONFLICT (job_name) DO NOTHING;

INSERT INTO connector_health (connector, kind, state, evidence) VALUES
    ('ghl_payments', 'api', 'dormant',
     '{"note": "DEC-002 provider. Tenant checkout verified rendering a live Stripe path 2026-08-07. Blocked on HT-005 (new JPD store) before offers can be created in a filterable namespace."}')
ON CONFLICT (connector) DO NOTHING;
