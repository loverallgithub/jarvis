-- ============================================================================
-- JPD 001 — core schema
--
-- Every table here exists because something in Pimlico was missing. Where a
-- constraint looks paranoid, it is discharging a specific observed failure;
-- the constraint carries the citation.
--
-- HARD RULE, learned three times in Pimlico: a CHECK constraint that is too
-- narrow for its own values is worse than no constraint. Every CHECK below
-- lists the complete value set used by the code. If you add a status, ALTER
-- the constraint in a new migration AND update this file.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- DISCOVERY
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sources (
    id                 BIGSERIAL PRIMARY KEY,
    name               TEXT        NOT NULL UNIQUE,
    kind               TEXT        NOT NULL CHECK (kind IN ('api','rss','browser','human')),
    source_type        TEXT        NOT NULL CHECK (source_type IN
                                     ('search','launch','community','review','filing','authority')),
    config             JSONB       NOT NULL DEFAULT '{}'::jsonb,
    enabled            BOOLEAN     NOT NULL DEFAULT TRUE,
    -- health_state is COMPUTED from observed yield + probes, never hand-set.
    -- (C3: Pimlico's `dormant` was a hand-set registry flag, so a source
    --  returning 0 items every day for weeks could never be flagged.)
    health_state       TEXT        NOT NULL DEFAULT 'live'
                                   CHECK (health_state IN ('live','degraded','dormant')),
    zero_yield_streak  INT         NOT NULL DEFAULT 0,
    fail_streak        INT         NOT NULL DEFAULT 0,
    last_yield_at      TIMESTAMPTZ,
    weight             NUMERIC(4,2) NOT NULL DEFAULT 1.00,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS signals (
    id            BIGSERIAL PRIMARY KEY,
    source_id     BIGINT      NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    external_id   TEXT        NOT NULL,
    concept       TEXT        NOT NULL,
    body          TEXT,
    url           TEXT,
    observed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    embedding_id  TEXT,
    cluster_id    BIGINT,
    raw           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (source_id, external_id)
);
CREATE INDEX IF NOT EXISTS signals_observed_idx ON signals (observed_at DESC);
CREATE INDEX IF NOT EXISTS signals_cluster_idx  ON signals (cluster_id);

CREATE TABLE IF NOT EXISTS clusters (
    id            BIGSERIAL PRIMARY KEY,
    label         TEXT,
    centroid      JSONB,
    member_count  INT         NOT NULL DEFAULT 0,
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Gate thresholds are ROWS, not constants (03-PIPELINE A4). Changing a gate
-- is an UPDATE, not a redeploy.
CREATE TABLE IF NOT EXISTS gate_thresholds (
    gate        TEXT PRIMARY KEY,
    threshold   NUMERIC(10,4) NOT NULL,
    comparator  TEXT NOT NULL DEFAULT '>=' CHECK (comparator IN ('>=','<=','>','<','=')),
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    rationale   TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- C6: Pimlico's near-miss census lived in per-process memory and was lost on
-- every restart, so three weeks of zero promotions could not be diagnosed.
-- Persisted, "what would have promoted at severity >= 3.5?" is a SQL query.
CREATE TABLE IF NOT EXISTS gate_evaluations (
    id            BIGSERIAL PRIMARY KEY,
    run_id        BIGINT,
    cluster_id    BIGINT REFERENCES clusters(id) ON DELETE CASCADE,
    gate          TEXT          NOT NULL,
    value         NUMERIC(10,4) NOT NULL,
    threshold     NUMERIC(10,4) NOT NULL,
    passed        BOOLEAN       NOT NULL,
    evaluated_at  TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS gate_eval_cluster_idx ON gate_evaluations (cluster_id, evaluated_at DESC);
CREATE INDEX IF NOT EXISTS gate_eval_gate_idx    ON gate_evaluations (gate, passed);

CREATE TABLE IF NOT EXISTS needs (
    id             BIGSERIAL PRIMARY KEY,
    cluster_id     BIGINT REFERENCES clusters(id) ON DELETE SET NULL,
    title          TEXT NOT NULL,
    pain_statement TEXT,
    audience       TEXT,
    status         TEXT NOT NULL DEFAULT 'candidate'
                   CHECK (status IN ('candidate','promoted','parked','rejected','in_progress','shipped')),
    score          NUMERIC(4,2),
    -- gap stays NULL until Phase B produces competitive data. Pimlico weighted
    -- `gap` at 0.25 — its second-highest weight — with no competitive data at all.
    gap            NUMERIC(4,2),
    promoted_by    TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- VOICES — who said it (DEC-004)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS voices (
    id            BIGSERIAL PRIMARY KEY,
    kind          TEXT NOT NULL CHECK (kind IN ('person','company')),
    display_name  TEXT NOT NULL,
    handle        TEXT,
    platform      TEXT NOT NULL,
    profile_url   TEXT,
    org_name      TEXT,
    org_domain    TEXT,
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
    enriched_at   TIMESTAMPTZ,
    contactable   BOOLEAN NOT NULL DEFAULT FALSE,
    contact_ref   TEXT,
    -- NON-NEGOTIABLE: defaults TRUE. Community-sourced authors are evidence,
    -- never a mailing list. Promotion to contactable requires an explicit
    -- lawful basis recorded in lawful_basis.
    do_not_contact BOOLEAN NOT NULL DEFAULT TRUE,
    lawful_basis   TEXT,
    UNIQUE (platform, handle)
);

CREATE TABLE IF NOT EXISTS voice_mentions (
    id           BIGSERIAL PRIMARY KEY,
    voice_id     BIGINT NOT NULL REFERENCES voices(id) ON DELETE CASCADE,
    signal_id    BIGINT REFERENCES signals(id) ON DELETE CASCADE,
    need_id      BIGINT REFERENCES needs(id) ON DELETE SET NULL,
    evidence_id  BIGINT,
    stance       TEXT NOT NULL CHECK (stance IN
                   ('reports_pain','requests_solution','offers_workaround',
                    'sells_alternative','endorses')),
    quote        TEXT,
    observed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS voice_mentions_need_idx  ON voice_mentions (need_id);
CREATE INDEX IF NOT EXISTS voice_mentions_voice_idx ON voice_mentions (voice_id);

-- ---------------------------------------------------------------------------
-- EVIDENCE & GROUNDING — C4
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS evidence (
    id               BIGSERIAL PRIMARY KEY,
    url              TEXT,
    sha256           TEXT        NOT NULL,
    fetched_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    http_status      INT,
    mime             TEXT,
    snippet          TEXT,
    full_artifact_id BIGINT,
    -- 'paraphrase' is REJECTED by the publish predicate (DEC-003, TubeOnAI).
    -- A summary can promote a need; it can never back a published claim.
    source_kind      TEXT NOT NULL DEFAULT 'primary'
                     CHECK (source_kind IN ('primary','paraphrase','derived')),
    captured_by_step TEXT,
    live_at_capture  BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS evidence_sha_idx ON evidence (sha256);

CREATE TABLE IF NOT EXISTS dossiers (
    id         BIGSERIAL PRIMARY KEY,
    need_id    BIGINT NOT NULL REFERENCES needs(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('need','research','competitive','pricing')),
    body       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- SOLUTIONS & THE THREE TIERS
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS solutions (
    id         BIGSERIAL PRIMARY KEY,
    need_id    BIGINT NOT NULL REFERENCES needs(id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'draft'
               CHECK (status IN ('draft','roadmap','instructions','deployed','published','withdrawn')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS artifacts (
    id           BIGSERIAL PRIMARY KEY,
    solution_id  BIGINT NOT NULL REFERENCES solutions(id) ON DELETE CASCADE,
    tier         TEXT NOT NULL CHECK (tier IN ('roadmap','instructions','deployed')),
    kind         TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    bytes        BIGINT NOT NULL,
    storage_uri  TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS artifacts_solution_tier_idx ON artifacts (solution_id, tier);

-- claims.evidence_id NOT NULL is the whole game: a deliverable with an
-- uncited factual claim cannot be published, because the DATABASE refuses it.
-- Pimlico had no citation field anywhere and sold 27.5k-word products that
-- were pure model recall.
CREATE TABLE IF NOT EXISTS claims (
    id             BIGSERIAL PRIMARY KEY,
    deliverable_id BIGINT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    text           TEXT   NOT NULL,
    evidence_id    BIGINT NOT NULL REFERENCES evidence(id) ON DELETE RESTRICT,
    confidence     NUMERIC(4,3),
    verified_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS acceptance_tests (
    id           BIGSERIAL PRIMARY KEY,
    solution_id  BIGINT NOT NULL REFERENCES solutions(id) ON DELETE CASCADE,
    tier         TEXT NOT NULL CHECK (tier IN ('roadmap','instructions','deployed')),
    name         TEXT NOT NULL,
    command      TEXT NOT NULL,
    expected     TEXT NOT NULL,
    last_result  TEXT CHECK (last_result IN ('pass','fail','error','never_run')),
    last_run_at  TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- COMMERCE — exists before any generation code (C5)
-- ---------------------------------------------------------------------------

-- price_minor is an INTEGER IN MINOR UNITS. Pimlico listed every product at
-- 100x because a float euro/cent confusion went unnoticed. Integers-in-cents
-- plus a read-back guard makes that bug unrepresentable.
CREATE TABLE IF NOT EXISTS offers (
    id           BIGSERIAL PRIMARY KEY,
    solution_id  BIGINT NOT NULL REFERENCES solutions(id) ON DELETE CASCADE,
    tier         TEXT NOT NULL CHECK (tier IN ('roadmap','instructions','deployed')),
    currency     TEXT NOT NULL DEFAULT 'EUR' CHECK (currency ~ '^[A-Z]{3}$'),
    price_minor  BIGINT NOT NULL CHECK (price_minor > 0),
    external_ref TEXT,
    store_ref    TEXT,
    live         BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (solution_id, tier)
);

CREATE TABLE IF NOT EXISTS orders (
    id              BIGSERIAL PRIMARY KEY,
    offer_id        BIGINT NOT NULL REFERENCES offers(id) ON DELETE RESTRICT,
    buyer_email     TEXT,
    buyer_ref       TEXT NOT NULL,
    amount_minor    BIGINT NOT NULL,
    currency        TEXT NOT NULL,
    provider        TEXT NOT NULL,
    provider_ref    TEXT NOT NULL,
    -- A failed signature cannot fulfil. Pimlico treated any amount > 0 as a
    -- paid order and would mint a EUR297 product for amount: 1.
    signature_valid BOOLEAN NOT NULL DEFAULT FALSE,
    amount_matched  BOOLEAN NOT NULL DEFAULT FALSE,
    status          TEXT NOT NULL DEFAULT 'received'
                    CHECK (status IN ('received','verified','rejected','fulfilled','refunded')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, provider_ref)
);

CREATE TABLE IF NOT EXISTS entitlements (
    id          BIGSERIAL PRIMARY KEY,
    order_id    BIGINT NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    buyer_ref   TEXT   NOT NULL,
    solution_id BIGINT NOT NULL REFERENCES solutions(id) ON DELETE RESTRICT,
    tier        TEXT   NOT NULL CHECK (tier IN ('roadmap','instructions','deployed')),
    granted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at  TIMESTAMPTZ,
    UNIQUE (order_id)
);

CREATE TABLE IF NOT EXISTS fulfilments (
    id              BIGSERIAL PRIMARY KEY,
    entitlement_id  BIGINT NOT NULL REFERENCES entitlements(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','delivered','failed','blocked_on_human')),
    artifact_id     BIGINT REFERENCES artifacts(id) ON DELETE RESTRICT,
    delivered_at    TIMESTAMPTZ,
    channel         TEXT,
    evidence        JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS upgrades (
    id                  BIGSERIAL PRIMARY KEY,
    from_entitlement_id BIGINT NOT NULL REFERENCES entitlements(id) ON DELETE CASCADE,
    to_tier             TEXT NOT NULL CHECK (to_tier IN ('roadmap','instructions','deployed')),
    price_delta_minor   BIGINT NOT NULL CHECK (price_delta_minor > 0),
    order_id            BIGINT REFERENCES orders(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- RUNTIME — C1
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS runs (
    id                BIGSERIAL PRIMARY KEY,
    need_id           BIGINT REFERENCES needs(id) ON DELETE SET NULL,
    solution_id       BIGINT REFERENCES solutions(id) ON DELETE SET NULL,
    phase             TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running','paused','blocked_on_human','completed','failed','killed')),
    -- Pimlico had a lease_owner column that appeared in NO WHERE clause, so
    -- killing a run resurrected it on the next tick. Every mutation of a run
    -- or its steps is guarded on this column.
    lease_owner       TEXT,
    lease_expires_at  TIMESTAMPTZ,
    cost_usd          NUMERIC(12,6) NOT NULL DEFAULT 0,
    kill_requested    BOOLEAN NOT NULL DEFAULT FALSE,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS runs_status_idx ON runs (status, started_at DESC);

CREATE TABLE IF NOT EXISTS steps (
    id             BIGSERIAL PRIMARY KEY,
    run_id         BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_id        TEXT   NOT NULL,
    phase          TEXT   NOT NULL,
    -- NOT NULL with a CHECK, no default, no 'unknown'. Pimlico persisted
    -- status=None for seven consecutive days and nothing noticed.
    status         TEXT   NOT NULL CHECK (status IN
                     ('running','succeeded','failed','blocked_on_human','skipped_dormant','quarantined')),
    attempt        INT    NOT NULL DEFAULT 1,
    -- repair_count is DURABLE and is never reset by a transition. Pimlico's
    -- repair guard tested `attempts`, which advance() reset to 0 on every
    -- transition, so the guard was always true and the loop never terminated.
    repair_count   INT    NOT NULL DEFAULT 0,
    accepted       BOOLEAN,
    acceptance_reason TEXT,
    idempotency_value TEXT NOT NULL DEFAULT '',
    result_json    JSONB  NOT NULL DEFAULT '{}'::jsonb,
    evidence_json  JSONB  NOT NULL DEFAULT '[]'::jsonb,
    cost_usd       NUMERIC(12,6) NOT NULL DEFAULT 0,
    lease_owner    TEXT,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS steps_run_idx ON steps (run_id, started_at DESC);
-- Idempotency: one succeeded row per (run, step, key). Partial index so
-- failures may be retried but successes may not be duplicated.
CREATE UNIQUE INDEX IF NOT EXISTS steps_idempotency_idx
    ON steps (run_id, step_id, idempotency_value) WHERE status = 'succeeded';

-- C7
CREATE TABLE IF NOT EXISTS human_tasks (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              BIGINT REFERENCES runs(id) ON DELETE CASCADE,
    step_row_id         BIGINT REFERENCES steps(id) ON DELETE CASCADE,
    ref                 TEXT NOT NULL UNIQUE,
    type                TEXT NOT NULL,
    title               TEXT NOT NULL,
    -- `why` is NOT NULL on purpose: Pimlico's "USER ACTION" bullets were
    -- skipped for weeks because no consequence was ever stated.
    why                 TEXT NOT NULL,
    how_md              TEXT NOT NULL,
    where_url           TEXT,
    verify_command      TEXT,
    reply_schema        JSONB NOT NULL DEFAULT '{}'::jsonb,
    status              TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open','replied','skipped','expired','cancelled')),
    assigned_channel    TEXT,
    telegram_message_id BIGINT,
    telegram_thread_id  BIGINT,
    expires_at          TIMESTAMPTZ NOT NULL,
    reply_json          JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at         TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS human_tasks_open_idx ON human_tasks (status, created_at);

-- ---------------------------------------------------------------------------
-- CONNECTORS — C3
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS connector_health (
    connector          TEXT PRIMARY KEY,
    kind               TEXT NOT NULL CHECK (kind IN ('api','rss','browser','human')),
    state              TEXT NOT NULL DEFAULT 'dormant'
                       CHECK (state IN ('live','degraded','dormant')),
    last_probe_at      TIMESTAMPTZ,
    last_contract_at   TIMESTAMPTZ,
    fail_streak        INT NOT NULL DEFAULT 0,
    zero_yield_streak  INT NOT NULL DEFAULT 0,
    evidence           JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Nothing in dead_letter can reach a publish path. This is where the entire
-- Sintra/LinkedIn class of failure terminates.
CREATE TABLE IF NOT EXISTS dead_letter (
    id          BIGSERIAL PRIMARY KEY,
    connector   TEXT NOT NULL,
    payload_raw TEXT NOT NULL,
    reason      TEXT NOT NULL,
    run_id      BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- CHECKPOINTS — the machine-readable half of resume (architecture §8)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS checkpoints (
    id             BIGSERIAL PRIMARY KEY,
    run_id         BIGINT REFERENCES runs(id) ON DELETE CASCADE,
    phase          TEXT NOT NULL,
    label          TEXT NOT NULL,
    reason         TEXT NOT NULL,
    state_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
    resumable_from TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS checkpoints_run_idx ON checkpoints (run_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- INTEGRITY & COST
-- ---------------------------------------------------------------------------

-- C8: source files silently reverted on this host and the mechanism is still
-- unidentified. Until it is found, treat any on-disk file as unproven.
CREATE TABLE IF NOT EXISTS source_manifest (
    path           TEXT NOT NULL,
    sha256         TEXT NOT NULL,
    image_tag      TEXT NOT NULL,
    verified_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    drift_detected BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (path, image_tag)
);

CREATE TABLE IF NOT EXISTS llm_usage (
    id         BIGSERIAL PRIMARY KEY,
    run_id     BIGINT,
    step_id    TEXT,
    model      TEXT NOT NULL,
    purpose    TEXT,
    tokens_in  INT NOT NULL DEFAULT 0,
    tokens_out INT NOT NULL DEFAULT 0,
    cost_usd   NUMERIC(12,6) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- C2: an untested detector is not a detector. AlertNeverTripped fires if any
-- rule has not been synthetically verified in 10 days.
CREATE TABLE IF NOT EXISTS alert_synthetics (
    alert_name      TEXT PRIMARY KEY,
    last_tripped_at TIMESTAMPTZ,
    last_result     TEXT CHECK (last_result IN ('fired','did_not_fire','error','never_run')),
    detail          TEXT
);

-- Freshness alerts are derived as expected_interval * 1.5 from this registry,
-- so changing a schedule updates its alert automatically.
CREATE TABLE IF NOT EXISTS job_registry (
    job_name            TEXT PRIMARY KEY,
    expected_interval_s INT NOT NULL CHECK (expected_interval_s > 0),
    last_success_at     TIMESTAMPTZ,
    enabled             BOOLEAN NOT NULL DEFAULT TRUE
);

-- Every cursor is stored WITH the highest value actually observed, so it can
-- be clamped. hermes:n8n:last_seen_execution sat at 1757 against a real max
-- id of 34 and blinded Pimlico's failure watcher for weeks.
CREATE TABLE IF NOT EXISTS watermarks (
    name         TEXT PRIMARY KEY,
    value        BIGINT NOT NULL DEFAULT 0,
    observed_max BIGINT NOT NULL DEFAULT 0,
    clamped_at   TIMESTAMPTZ,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
