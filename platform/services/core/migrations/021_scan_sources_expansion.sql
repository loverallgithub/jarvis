-- ============================================================================
-- JPD 021 — operator scan-source expansion: Greg Isenberg (YT), This Week in
-- Startups (YT), Greg's Letter (Substack RSS), AppSumo (deals API)
--
-- Operator request 2026-08-16. Everything here was probed from this VPS
-- before the connector was written (the sources.py header rule):
--
--   * yt_greg_isenberg — handle verified via channels.list?forHandle:
--     resolves to "Greg Isenberg", 695K subs.
--   * yt_this_week_in_startups — seeded by CHANNEL_ID, not handle.
--     🔴 @thisweekinstartups is a 1,980-subscriber squatter; the real channel
--     (353K subs, handle @startups) is UCkkhmBWfS7pILYIk0izkc3A. Verified via
--     search.list + channels.list 2026-08-16. Seeding the id takes the 0-unit
--     path and cannot resolve to the wrong channel.
--   * gregs_letter — the operator said "gregisenberg.com", but that site is a
--     Framer SPA with no feed; the newsletter lives on Substack and
--     latecheckout.substack.com/feed serves real RSS under the project UA.
--   * appsumo — /api/v2/deals/ JSON endpoint (the /rss/ path is an SPA-shell
--     200-with-HTML trap, per the IndieHackers post-mortem method).
--
-- NOT added, with reasons (so the next session does not re-probe blind):
--   * trends_vc — Cloudflare 403s every path from this datacenter IP (same
--     class as reddit). Needs a different egress or an email-ingest transport.
--   * exploding_topics — no public RSS exists (blog is Next.js, no feed, no
--     autodiscovery); their data API is a paid product needing a credential
--     decision.
--   * indie_hackers MRR — the connector remains in known-blocked: the site is
--     an SPA serving byte-identical shells; founder-reported MRR needs a
--     browser transport (HT-003-style), not a new URL.
--   * product_hunt — already live; nothing to add.
-- ============================================================================

INSERT INTO sources (name, kind, source_type, config, enabled) VALUES
    ('yt_greg_isenberg',          'api', 'authority', '{"channel": "Greg Isenberg",          "handle": "@gregisenberg",              "why": "community-led product ideas; niche demand spotting"}', TRUE),
    ('yt_this_week_in_startups',  'api', 'authority', '{"channel": "This Week in Startups",  "channel_id": "UCkkhmBWfS7pILYIk0izkc3A", "why": "startup market narratives; founder pain in interviews"}', TRUE),
    ('gregs_letter',              'rss', 'authority', '{"why": "Greg Isenberg newsletter; written form of the same community-led-product lens"}', TRUE),
    ('appsumo',                   'api', 'launch',    '{"why": "lifetime-deal marketplace; what indie buyers pay for RIGHT NOW, with review counts"}', TRUE)
ON CONFLICT (name) DO NOTHING;
