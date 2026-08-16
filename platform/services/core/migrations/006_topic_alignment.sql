-- ============================================================================
-- JPD 006 — TOPIC-ALIGNED SOURCE CONFIGURATION
--
-- 🔴 THE DEEPEST FINDING OF PHASE 4.
--
-- With every source pointed at a DIFFERENT subject, the funnel produced **zero
-- cross-source clusters** — measured at every similarity threshold from 0.30
-- down to 0.08, where clustering already over-merges into 27-member blobs.
-- The vocabularies genuinely do not overlap:
--
--   review    App Store complaints about Slack/Notion notifications
--   community GitHub CI failures, StackOverflow automation questions
--   search    "best way to reconcile bank accounts"
--   filing    SEC company names + risk-factor boilerplate
--   launch    Product Hunt launch titles
--
-- Nothing corroborates anything, because nothing is ABOUT the same thing.
--
-- This very likely explains why Pimlico's funnel never promoted despite 1,690
-- accumulated signals: more volume does not create corroboration when the
-- sources are independently scattered. Cross-source corroboration is not an
-- emergent property of harvesting widely — it has to be ARRANGED, by pointing
-- the sources at a shared problem domain.
--
-- Domain chosen from evidence, not taste: google_suggest independently
-- surfaced "best way to reconcile bank accounts", "reconcile credit card
-- statements" and "reconcile two excel spreadsheets" — real demand, already
-- observed, in a domain where B2B tooling exists and people pay for it.
-- ============================================================================

UPDATE sources SET config = config || jsonb_build_object('seeds', jsonb_build_array(
    'best way to reconcile',
    'software to automate invoice',
    'how to match invoices to',
    'automate accounts payable',
    'stop chasing unpaid invoices',
    'reconcile payments automatically'
)) WHERE name = 'google_suggest';

UPDATE sources SET config = config || jsonb_build_object('queries', jsonb_build_array(
    '"reconcile invoices"',
    '"accounts payable" manual',
    '"chasing invoices"',
    '"invoice matching"',
    '"billing reconciliation"',
    '"spend hours" invoices',
    '"bookkeeping" painful'
)) WHERE name = 'hacker_news';

UPDATE sources SET config = config || jsonb_build_object(
    'query', 'invoice reconciliation in:title state:open'
) WHERE name = 'github_issues';

UPDATE sources SET config = config || jsonb_build_object(
    'tagged', 'invoice'
) WHERE name = 'stackoverflow';

-- Accounting / invoicing apps. EVERY id resolved through the iTunes search API
-- before being written here — my first two guesses (1476785425, 429589752)
-- both returned NOT FOUND, which is precisely how WhatsApp and Starbucks ended
-- up seeded as "B2B tooling" in the first place. Guessing app ids is how that
-- mistake is made; looking them up is how it is not.
--   584606479  Intuit QuickBooks for Business
--   441880705  Xero Accounting for business
--   1052884030 FreshBooks Invoicing App
--   881629660  Wave: Small Business Software
--   710446064  Accounting App - Zoho Books
--   540236748  Invoice2go: Easy Invoice Maker
UPDATE sources SET config = config || jsonb_build_object('apps', jsonb_build_array(
    '584606479', '441880705', '1052884030', '881629660', '710446064', '540236748'
)) WHERE name = 'app_store_reviews';

UPDATE sources SET config = config || jsonb_build_object(
    'query', '"revenue recognition" "billing"',
    'lookback_days', 120
) WHERE name = 'sec_edgar';
