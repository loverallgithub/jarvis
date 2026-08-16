-- ============================================================================
-- JPD 016 — Phase F, MARKET
--
-- Three tables, one rule each.
--
--   positioning   ONE row per need. Positioning is a claim about the buyer's
--                 world, so it cites evidence like any other claim.
--   copy_blocks   Per TIER, per BLOCK. Three buyers are not one buyer, so the
--                 headline that converts an owner-operator is not the headline
--                 that converts a team with a budget.
--   sales_pages   The rendered artefact, content-addressed like every other
--                 deliverable, so what was reviewed is what shipped.
--
-- 🔴 WHY `citation_pct` IS PERSISTED ON copy_blocks AND sales_pages
--
-- Phase F is where an uncited claim stops being an internal quality problem and
-- becomes a PUBLIC PROMISE. `03-PIPELINE.md` F2 requires "every factual claim
-- cited"; measured on the phase C/D/E artifacts on 2026-08-09, actual coverage
-- of checkable assertions was 56.3% while the system reported "0 uncited
-- claims" — a number that was a NOT NULL constraint, not a measurement.
--
-- So marketing copy carries its coverage on the row, and the step gates on it.
-- Artifacts are not gated on coverage yet (that would silently change what
-- `offerable` means); copy is, because the blast radius is different.
-- ============================================================================

CREATE TABLE IF NOT EXISTS positioning (
    id          BIGSERIAL PRIMARY KEY,
    need_id     BIGINT NOT NULL REFERENCES needs(id) ON DELETE CASCADE,
    -- The buyer's own words, not invented adjectives (F1).
    pain_phrase TEXT   NOT NULL,
    audience    TEXT   NOT NULL,
    promise     TEXT   NOT NULL,
    proof       TEXT   NOT NULL DEFAULT '',
    -- Which voice said it, and where. Positioning that cannot point at a human
    -- being who said the thing is positioning we invented.
    voice_id    BIGINT REFERENCES voices(id) ON DELETE SET NULL,
    evidence_id BIGINT REFERENCES evidence(id) ON DELETE SET NULL,
    run_id      BIGINT REFERENCES runs(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (need_id)
);

CREATE TABLE IF NOT EXISTS copy_blocks (
    id          BIGSERIAL PRIMARY KEY,
    need_id     BIGINT NOT NULL REFERENCES needs(id) ON DELETE CASCADE,
    tier        TEXT   NOT NULL CHECK (tier IN ('roadmap','instructions','deployed')),
    block       TEXT   NOT NULL CHECK (block IN
                  ('headline','subhead','benefits','objections','faq')),
    body        TEXT   NOT NULL,
    -- Citation coverage of THIS block, measured at generation time.
    citation_pct     NUMERIC(5,2) NOT NULL DEFAULT 100.00,
    citation_checkable INT NOT NULL DEFAULT 0,
    cited_claim_ids  BIGINT[] NOT NULL DEFAULT '{}',
    approved_at TIMESTAMPTZ,
    run_id      BIGINT REFERENCES runs(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (need_id, tier, block)
);

CREATE INDEX IF NOT EXISTS copy_blocks_need_idx ON copy_blocks (need_id, tier);

CREATE TABLE IF NOT EXISTS sales_pages (
    id          BIGSERIAL PRIMARY KEY,
    need_id     BIGINT NOT NULL REFERENCES needs(id) ON DELETE CASCADE,
    -- Content-addressed, like artifacts: the sha is of the bytes on disk, so
    -- "the page we reviewed" and "the page we shipped" are the same object or
    -- provably are not.
    sha256      TEXT   NOT NULL,
    bytes       INT    NOT NULL,
    storage_uri TEXT   NOT NULL,
    tiers       INT    NOT NULL DEFAULT 0,
    citation_pct NUMERIC(5,2) NOT NULL DEFAULT 100.00,
    -- A page is PUBLISHABLE only when every tier on it has a live offer and
    -- coverage clears the floor. Default false; nothing publishes by accident.
    publishable BOOLEAN NOT NULL DEFAULT FALSE,
    published_at TIMESTAMPTZ,
    run_id      BIGINT REFERENCES runs(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (need_id)
);

-- ── outreach ────────────────────────────────────────────────────────────────
-- 🔴 One row per RECIPIENT per launch, written BEFORE anything is sent, with
-- the lawful basis and the quote being echoed back recorded on the row.
--
-- F5b's acceptance is that every recipient has a lawful basis, an unsubscribe
-- path, and a citation to their own words — and that the step REFUSES rather
-- than silently dropping anyone who fails. That refusal has to be auditable
-- after the fact, which means the intended list is persisted before the send,
-- not derived from whatever succeeded.
CREATE TABLE IF NOT EXISTS launch_recipients (
    id           BIGSERIAL PRIMARY KEY,
    need_id      BIGINT NOT NULL REFERENCES needs(id) ON DELETE CASCADE,
    voice_id     BIGINT NOT NULL REFERENCES voices(id) ON DELETE CASCADE,
    tier         TEXT   NOT NULL,
    stance       TEXT   NOT NULL,
    quote        TEXT   NOT NULL,
    evidence_id  BIGINT REFERENCES evidence(id) ON DELETE SET NULL,
    lawful_basis TEXT   NOT NULL,
    contact_ref  TEXT   NOT NULL,
    unsubscribe_url TEXT NOT NULL,
    status       TEXT   NOT NULL DEFAULT 'planned'
                 CHECK (status IN ('planned','approved','sent','failed','skipped')),
    sent_at      TIMESTAMPTZ,
    error        TEXT,
    run_id       BIGINT REFERENCES runs(id) ON DELETE SET NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (need_id, voice_id)
);

CREATE INDEX IF NOT EXISTS launch_recipients_need_idx
    ON launch_recipients (need_id, status);
