-- ============================================================================
-- JPD 020 — authority roster, batch 2: twenty operator-selected channels
--
-- Operator decision 2026-08-16 (second expansion the same day; see 019).
-- Handles were supplied by the operator, read off each channel's URL — the
-- connector still refuses to guess, and identity remains DATA seeded here so
-- a fresh database harvests the right channels with no manual UPDATE.
--
-- Same load-bearing rule as 002/019: every row is source_type='authority',
-- so no amount of creator channels can self-corroborate the cross-source
-- gate. Twenty-eight channels are still ONE source type.
--
-- Quota note (HT-002): each channel costs 2 units/harvest on the cheap path;
-- the full 28-channel roster costs 56 of the 10,000 daily units.
--
-- ON CONFLICT DO NOTHING so the migration is safe on a database where any
-- row was already inserted by hand before this file shipped.
-- ============================================================================

INSERT INTO sources (name, kind, source_type, config, enabled) VALUES
    ('yt_julian_goldie',          'api', 'authority', '{"channel": "Julian Goldie",          "handle": "@JulianGoldieSEO",       "why": "AI SEO and content-agency playbooks; link-building service gaps"}', TRUE),
    ('yt_affiliate_marketing_dude','api','authority', '{"channel": "Affiliate Marketing Dude","handle": "@affiliatemarketingdude","why": "affiliate monetisation patterns; niche-site demand signals"}', TRUE),
    ('yt_simplilearn',            'api', 'authority', '{"channel": "Simplilearn",            "handle": "@SimplilearnOfficial",   "why": "certification-training demand; which skills buyers pay to learn"}', TRUE),
    ('yt_chase_h_ai',             'api', 'authority', '{"channel": "Chase H",                "handle": "@Chase-H-AI",            "why": "AI automation content; operator-selected 2026-08-16 roster expansion"}', TRUE),
    ('yt_starter_story',          'api', 'authority', '{"channel": "Starter Story",          "handle": "@starterstory",          "why": "founder case studies with revenue numbers; niche demand evidence"}', TRUE),
    ('yt_shane_hummus',           'api', 'authority', '{"channel": "Shane Hummus",           "handle": "@ShaneHummus",           "why": "career and skills ROI content; upskilling-path demand"}', TRUE),
    ('yt_heygen',                 'api', 'authority', '{"channel": "HeyGen",                 "handle": "@heygen_official",       "why": "AI avatar/video ecosystem; user asks around video automation"}', TRUE),
    ('yt_npo_start',              'api', 'authority', '{"channel": "NPO Start",              "handle": "@NPO_Start",             "why": "operator-selected 2026-08-16 roster expansion"}', TRUE),
    ('yt_sharran',                'api', 'authority', '{"channel": "Sharran Srivatsaa",      "handle": "@sharran",               "why": "sales and scaling frameworks; high-ticket service demand"}', TRUE),
    ('yt_brad_sugars',            'api', 'authority', '{"channel": "Brad Sugars",            "handle": "@bradleysugars",         "why": "business coaching frameworks; SMB operational pain"}', TRUE),
    ('yt_joanna_wiebe',           'api', 'authority', '{"channel": "Joanna Wiebe",           "handle": "@joanna-wiebe",          "why": "conversion copywriting; messaging and funnel gaps"}', TRUE),
    ('yt_mark_kashef',            'api', 'authority', '{"channel": "Mark Kashef",            "handle": "@Mark_Kashef",           "why": "AI freelancing and prompt-engineering services; client demand signals"}', TRUE),
    ('yt_rob_the_ai_guy',         'api', 'authority', '{"channel": "Rob the AI Guy",         "handle": "@realrobtheaiguy",       "why": "AI automation for local businesses; unmet implementation asks"}', TRUE),
    ('yt_its_keaton',             'api', 'authority', '{"channel": "Keaton",                 "handle": "@ItsKeaton",             "why": "operator-selected 2026-08-16 roster expansion"}', TRUE),
    ('yt_solopreneur',            'api', 'authority', '{"channel": "Solopreneur",            "handle": "@Solopreneur",           "why": "solo-founder tooling and productised-service patterns"}', TRUE),
    ('yt_financial_news_oraat',   'api', 'authority', '{"channel": "Financial News ORaaT",   "handle": "@FinancialNewsORaaT",    "why": "operator-selected 2026-08-16 roster expansion"}', TRUE),
    ('yt_mark_j_kohler',          'api', 'authority', '{"channel": "Mark J Kohler",          "handle": "@MarkJKohler",           "why": "tax and legal strategy for small business; compliance pain points"}', TRUE),
    ('yt_life_insurance_academy', 'api', 'authority', '{"channel": "Life Insurance Academy", "handle": "@LifeInsuranceAcademy",  "why": "insurance sales training; agent tooling and lead-gen gaps"}', TRUE),
    ('yt_simon_squibb',           'api', 'authority', '{"channel": "Simon Squibb",           "handle": "@SimonSquibb",           "why": "first-time founder help; early-stage demand signals"}', TRUE),
    ('yt_diary_of_a_ceo',         'api', 'authority', '{"channel": "The Diary Of A CEO",     "handle": "@TheDiaryOfACEO",        "why": "founder and operator interviews; market and demand narratives"}', TRUE)
ON CONFLICT (name) DO NOTHING;
