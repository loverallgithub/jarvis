-- ============================================================================
-- JPD 004 — the console
--
-- Pimlico's Telegram client posts every message — briefs, alerts, approvals,
-- errors — into ONE chat with ONE chat_id and no message_thread_id. An approval
-- that stalled a €297 build sat between a metrics dump and a Reddit drip log
-- and was missed for five days.
--
-- Streams are ROWS, not constants: adding a topic is an INSERT.
-- ============================================================================

CREATE TABLE IF NOT EXISTS telegram_streams (
    stream      TEXT PRIMARY KEY CHECK (stream IN
                  ('decisions','human-tasks','sintra','discoveries','revenue','alerts')),
    chat_id     BIGINT,
    thread_id   BIGINT,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    purpose     TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seeded WITHOUT ids. A stream with no chat_id cannot be posted to, and the
-- telegram connector stays dormant until HT-001 fills these in. Seeding a
-- placeholder id would mean posts silently landing in the wrong chat.
INSERT INTO telegram_streams (stream, purpose) VALUES
    ('decisions',   'Gate approvals, price approvals, publish approvals'),
    ('human-tasks', 'The blocking queue — everything with a verify command'),
    ('sintra',      'Sintra instruction cards — paste the prompt, reply with the output'),
    ('discoveries', 'Needs promoted, with the gate census that let them through'),
    ('revenue',     'Orders, upgrades, fulfilments'),
    ('alerts',      'Connector dormancy, synthetic-failure results, integrity drift')
ON CONFLICT (stream) DO NOTHING;

-- ---------------------------------------------------------------------------
-- HUMAN TASKS — additions
-- ---------------------------------------------------------------------------

-- Which topic the card was posted into.
ALTER TABLE human_tasks ADD COLUMN IF NOT EXISTS stream TEXT
    REFERENCES telegram_streams(stream) ON DELETE SET NULL;

-- Decision cards are human tasks with a fixed option set — one queue, one
-- lifecycle, one place to look for "what is blocked".
ALTER TABLE human_tasks ADD COLUMN IF NOT EXISTS options JSONB;

-- Idempotency: re-running a blocked step must find its EXISTING task rather
-- than posting a second card. Without this, every resume attempt spams the
-- topic and the operator cannot tell which card is live.
ALTER TABLE human_tasks ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS human_tasks_idem_idx
    ON human_tasks (idempotency_key) WHERE idempotency_key IS NOT NULL;

-- The step this task is blocking, so `jpd resume` can say what is waiting.
ALTER TABLE human_tasks ADD COLUMN IF NOT EXISTS step_id TEXT;

-- Why a reply was rejected. A failed parse RE-ASKS rather than persisting
-- garbage, and the operator must be told what was wrong with their answer.
ALTER TABLE human_tasks ADD COLUMN IF NOT EXISTS last_parse_error TEXT;
ALTER TABLE human_tasks ADD COLUMN IF NOT EXISTS reply_attempts INT NOT NULL DEFAULT 0;

-- 'skipped' already exists in the status CHECK; SKIP is an explicit, recorded
-- operator decision, never a silent timeout.
ALTER TABLE human_tasks ADD COLUMN IF NOT EXISTS skip_reason TEXT;

CREATE INDEX IF NOT EXISTS human_tasks_run_idx ON human_tasks (run_id, status);

-- ---------------------------------------------------------------------------
-- REPLY AUDIT
-- ---------------------------------------------------------------------------
-- Every inbound Telegram message that looked like a reply, recorded BEFORE it
-- is interpreted — including ones that failed to parse. Same reasoning as
-- provider_events on the money path: "we never saw it" and "we could not
-- understand it" must stay distinguishable.
CREATE TABLE IF NOT EXISTS telegram_replies (
    id                  BIGSERIAL PRIMARY KEY,
    update_id           BIGINT NOT NULL UNIQUE,
    chat_id             BIGINT,
    thread_id           BIGINT,
    reply_to_message_id BIGINT,
    from_user_id        BIGINT,
    text_raw            TEXT,
    matched_task_id     BIGINT REFERENCES human_tasks(id) ON DELETE SET NULL,
    accepted            BOOLEAN NOT NULL DEFAULT FALSE,
    reject_reason       TEXT,
    received_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS telegram_replies_task_idx ON telegram_replies (matched_task_id);

-- ---------------------------------------------------------------------------
-- NOTIFICATION CHANNELS
-- ---------------------------------------------------------------------------
-- Closes the phase-1 gap: send_delivery had no live channel and honestly
-- recorded skipped_dormant. Order matters — first live channel wins, the rest
-- are fallbacks. GHL conversations and Mailgun are both PROVEN in Pimlico.
CREATE TABLE IF NOT EXISTS notification_channels (
    channel     TEXT PRIMARY KEY,
    connector   TEXT NOT NULL,
    priority    INT  NOT NULL,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    note        TEXT
);

INSERT INTO notification_channels (channel, connector, priority, note) VALUES
    ('ghl',      'ghl',      1, 'GHLClient.send_email(contact_id, subject, html) — html= not body=. Proven in Pimlico 2026-07-31.'),
    ('mailgun',  'mailgun',  2, 'Fallback. Domain must be .com; proven delivering 2026-07-31.'),
    ('telegram', 'telegram', 3, 'Operator-visible fallback so a delivery is never silent, even if the buyer channel is down.')
ON CONFLICT (channel) DO NOTHING;

INSERT INTO job_registry (job_name, expected_interval_s) VALUES
    ('console.poll_replies', 60),
    ('console.expire_tasks', 300)
ON CONFLICT (job_name) DO NOTHING;
