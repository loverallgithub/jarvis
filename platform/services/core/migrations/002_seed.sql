-- ============================================================================
-- JPD 002 — seed rows
--
-- Gate thresholds and the source registry are DATA, not code. Retuning a gate
-- is an UPDATE. Adding a creator channel is an INSERT. Neither is a redeploy.
-- ============================================================================

INSERT INTO gate_thresholds (gate, threshold, comparator, rationale) VALUES
    ('frequency',        5,   '>=', 'Independent mentions. One person''s complaint is not a market.'),
    ('severity',         4.0, '>=', 'Averaged over PAIN evidence only — launches do not count.'),
    ('cross_source',     2,   '>=', 'Distinct source_type. authority CANNOT self-corroborate.'),
    ('recency_days',     7,   '<=', 'Days since the most recent mention. Kills dead trends.'),
    ('commercial_intent',1,   '>=', 'Signals of spend/tooling/hiring. Pain without budget is not a product.'),
    ('distinct_voices',  3,   '>=', 'Distinct voices behind the mentions. 5 mentions from 1 person is one loud person.')
ON CONFLICT (gate) DO NOTHING;

-- Sources. Everything ships `enabled` but health_state is COMPUTED — a source
-- that yields nothing walks itself toward dormant regardless of this row.
INSERT INTO sources (name, kind, source_type, config, enabled) VALUES
    ('google_suggest',    'api',     'search',    '{}', TRUE),
    ('google_trends',     'api',     'search',    '{}', TRUE),
    ('product_hunt',      'api',     'launch',    '{}', TRUE),
    ('indie_hackers',     'rss',     'launch',    '{}', TRUE),
    ('hacker_news',       'api',     'community', '{}', TRUE),
    ('reddit',            'api',     'community', '{"rate_limit_s": 2}', TRUE),
    ('stackoverflow',     'api',     'community', '{}', TRUE),
    ('github_issues',     'api',     'community', '{}', TRUE),
    ('discourse',         'api',     'community', '{}', TRUE),
    ('app_store_reviews', 'api',     'review',    '{"stars_max": 3}', TRUE),
    ('sec_edgar',         'api',     'filing',    '{"exclude_sic": [6000, 6799]}', TRUE)
ON CONFLICT (name) DO NOTHING;

-- authority — creator channels. All share source_type='authority' precisely so
-- they can never self-corroborate the cross-source gate (same lesson as
-- stackoverflow + github_issues both being 'community').
INSERT INTO sources (name, kind, source_type, config, enabled) VALUES
    ('yt_alex_hormozi',   'api',     'authority', '{"channel": "Alex Hormozi",   "why": "offer construction, pricing, value-equation framing"}', TRUE),
    ('yt_leila_hormozi',  'api',     'authority', '{"channel": "Leila Hormozi",  "why": "operational and people-systems pain at scale"}', TRUE),
    ('yt_codie_sanchez',  'api',     'authority', '{"channel": "Codie Sanchez",  "why": "boring, overlooked, high-willingness-to-pay niches"}', TRUE),
    ('yt_liam_ottley',    'api',     'authority', '{"channel": "Liam Ottley",    "why": "AI-automation agency demand; enumerates unmet client asks"}', TRUE),
    ('yt_liam_evans',     'api',     'authority', '{"channel": "Liam Evans",     "why": "automation build patterns and tooling gaps"}', TRUE),
    ('yt_jack_roberts',   'api',     'authority', '{"channel": "Jack Roberts",   "why": "productised-service and delivery-model gaps"}', TRUE),
    ('skool',             'browser', 'authority', '{"why": "paid-community threads = pain someone already paid to discuss", "no_public_api": true, "human_fallback": true}', TRUE),
    ('tubeonai',          'api',     'authority', '{"why": "transport accelerator for authority — summaries, not transcripts", "emits_source_kind": "paraphrase", "ships_dormant": true}', TRUE)
ON CONFLICT (name) DO NOTHING;

-- Connector health. Everything starts DORMANT and must earn `live` by passing
-- a contract test. A connector that has never been tested cannot emit.
INSERT INTO connector_health (connector, kind, state, evidence) VALUES
    ('google_suggest',    'api',     'dormant', '{"note": "untested"}'),
    ('google_trends',     'api',     'dormant', '{"note": "untested"}'),
    ('product_hunt',      'api',     'dormant', '{"note": "untested"}'),
    ('indie_hackers',     'rss',     'dormant', '{"note": "untested"}'),
    ('hacker_news',       'api',     'dormant', '{"note": "untested"}'),
    ('reddit',            'api',     'dormant', '{"note": "untested"}'),
    ('stackoverflow',     'api',     'dormant', '{"note": "untested"}'),
    ('github_issues',     'api',     'dormant', '{"note": "untested"}'),
    ('discourse',         'api',     'dormant', '{"note": "untested"}'),
    ('app_store_reviews', 'api',     'dormant', '{"note": "untested; returns 29 on demand but 0 at harvest in Pimlico — unexplained"}'),
    ('sec_edgar',         'api',     'dormant', '{"note": "untested"}'),
    ('youtube_data_v3',   'api',     'dormant', '{"note": "blocked on HT-002 — no API key"}'),
    ('skool',             'browser', 'dormant', '{"note": "blocked on HT-003 — no public API"}'),
    ('tubeonai',          'api',     'dormant', '{"note": "DEC-003. api.tubeonai.com verified a real API host 2026-08-07 (200 root, genuine 404 on nonsense path, different body hash). Endpoint contract UNVERIFIED — no published docs. Blocked on HT-006."}'),
    ('sintra',            'human',   'dormant', '{"note": "Cloudflare-blocked from this VPS. HUMAN connector by design — do not automate headlessly, that path produced the LinkedIn incident."}'),
    ('telegram',          'api',     'dormant', '{"note": "blocked on HT-001 — forum supergroup not created"}'),
    ('ghl',               'api',     'dormant', '{"note": "credential exists; new JPD store blocked on HT-005"}'),
    ('stripe',            'api',     'dormant', '{"note": "DEC-002 — same Stripe account as Pimlico, via GHL"}'),
    ('mailgun',           'api',     'dormant', '{"note": "proven working in Pimlico 2026-07-31"}'),
    ('you_com',           'api',     'dormant', '{"note": "research/grounding, lite tier $0.012/call"}'),
    ('databar',           'api',     'dormant', '{"note": "company enrichment — COMPANY voices only, never private individuals"}'),
    ('ollama',            'api',     'dormant', '{"note": "local embeddings + whisper fallback, zero API cost"}'),
    ('qdrant',            'api',     'dormant', '{"note": "local vector store"}')
ON CONFLICT (connector) DO NOTHING;

-- Job registry. Freshness alerts derive from expected_interval_s * 1.5.
INSERT INTO job_registry (job_name, expected_interval_s) VALUES
    ('discovery.harvest',      86400),
    ('connector.contract_test',  900),
    ('integrity.manifest_check', 3600),
    ('alert.synthetic_sweep',  604800)
ON CONFLICT (job_name) DO NOTHING;
