-- ============================================================================
-- JPD 019 — expand the authority roster: Nick Saraev, My First Million
--
-- Operator decision 2026-08-16: the operator supplied eight channel handles
-- for the authority sweep; six matched the rows seeded in 002 and these two
-- had nowhere to go. Same rules as the original six: source_type='authority'
-- so they can never self-corroborate the cross-source gate (see 002), and
-- identity is DATA — the handle is seeded here because the operator read it
-- off the channel URL, so a fresh database harvests the right channel with
-- no manual UPDATE step.
--
-- ON CONFLICT DO NOTHING so the migration is safe on a database where the
-- rows were already inserted by hand before this file shipped.
-- ============================================================================

INSERT INTO sources (name, kind, source_type, config, enabled) VALUES
    ('yt_nick_saraev',      'api', 'authority', '{"channel": "Nick Saraev",      "handle": "@nicksaraev",       "why": "automation-agency delivery playbooks; concrete unmet client asks"}', TRUE),
    ('yt_my_first_million', 'api', 'authority', '{"channel": "My First Million", "handle": "@MyFirstMillionPod", "why": "business-idea brainstorms; demand spotting in overlooked niches"}', TRUE)
ON CONFLICT (name) DO NOTHING;
