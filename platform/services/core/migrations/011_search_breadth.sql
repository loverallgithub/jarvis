-- ============================================================================
-- JPD 011 — search breadth
--
-- With the quality gate live, the first honest run produced **8 usable rows
-- from 15 live** — roughly half of every fetch yields nothing that can support
-- a claim (Cloudflare interstitials, JS-only pages, thin listicles).
--
-- The acceptance bar is 15 SUBSTANTIVE rows. Meeting it means attempting ~2x
-- that many pages. Widening the search is the honest response; narrowing the
-- definition of "evidence" would not be.
-- ============================================================================

INSERT INTO research_params (param, value, rationale) VALUES
    ('results_per_query', '10',
     'Measured yield is ~50% usable, so 6 queries x 10 results attempts ~60 pages to clear a bar of 15.')
ON CONFLICT (param) DO UPDATE SET value = EXCLUDED.value, rationale = EXCLUDED.rationale;
