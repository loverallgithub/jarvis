-- ============================================================================
-- JPD 010 — evidence QUALITY, not just quantity
--
-- The first real dossier met its numeric bar (21 live hash-verified rows) while
-- several of those rows were worthless. Verbatim, from `jpd research evidence`:
--
--   #2..#5   4,852 bytes   "Connecting to the iTunes Store."   <- App Store
--                                                                 URLs do not
--                                                                 render
--                                                                 server-side
--   #6..#8  92,056 bytes   "Google Search"                     <- search result
--                                                                 pages, not
--                                                                 content
--
-- Those are genuinely fetched and genuinely hashed. They are not evidence of
-- anything. A count that includes them is the same species of lie as Pimlico's
-- `processed=4` when all four Sintra prompts had failed — technically true,
-- practically false.
--
-- `substantive` is computed at capture time and the acceptance predicate counts
-- only substantive rows, so the exit criterion means what it says.
-- ============================================================================

ALTER TABLE evidence ADD COLUMN IF NOT EXISTS substantive BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS reject_reason TEXT;

CREATE INDEX IF NOT EXISTS evidence_substantive_idx
    ON evidence (need_id, substantive, live_at_capture);

-- Re-classify what is already stored, using the same rules the code now applies.
UPDATE evidence SET substantive = FALSE,
       reject_reason = 'placeholder page — no server-rendered content'
 WHERE body IS NOT NULL
   AND (body ILIKE 'Connecting to the iTunes Store%'
        OR body ILIKE 'Just a moment%'
        OR body ILIKE '%enable JavaScript%and%cookies%');

UPDATE evidence SET substantive = FALSE,
       reject_reason = 'search engine result page, not source content'
 WHERE url ~* '(google\.[a-z.]+/search|bing\.com/search|duckduckgo\.com/\?q=)';

UPDATE evidence SET substantive = FALSE,
       reject_reason = 'too thin to support a claim'
 WHERE length(coalesce(body, '')) < 500 AND substantive;

INSERT INTO research_params (param, value, rationale) VALUES
    ('min_body_chars', '500',
     'Below this a page cannot support a claim. Measured: the placeholder pages that polluted the first dossier were 4.8KB of markup and ~30 chars of text.'),
    ('max_claims_per_domain', '3',
     'worldmetrics.org produced the same three gaps twice from two captures of one site. Repetition from one domain is redundancy, not corroboration.')
ON CONFLICT (param) DO NOTHING;
