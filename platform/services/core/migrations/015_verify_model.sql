-- ============================================================================
-- JPD 015 — the fact-checker needs its own model and its own token budget
--
-- 🔴 Fact-checking ran on `claude-opus-5` with `max_tokens=200`. Opus emits a
-- thinking block first, so the budget was consumed before any text block
-- existed — `_text_of` returned None and every one of those checks recorded
-- "verification did not return a usable answer", which the code then treats as
-- NOT SUPPORTED (correctly — an unverifiable claim is not a verified one).
--
-- Result: 9 of 13 distinct failure reasons were the verifier breaking, not
-- claims failing. The remaining 4 were genuine and well-reasoned:
--   "the excerpt is only site navigation/menu content"
--   "the provided source text is almost entirely CSS/boilerplate"
--   "the excerpt never mentions Tipalti"
-- — which are really pointing at an EVIDENCE quality problem upstream.
--
-- Verification is a small, high-volume, low-creativity task. It gets haiku and
-- a budget that leaves room for an answer.
-- ============================================================================

INSERT INTO research_params (param, value, rationale) VALUES
    ('verify_model', 'claude-haiku-4-5-20251001',
     'Fact-checking is high-volume and low-creativity. Also: haiku does not spend the token budget on a thinking block, which is what silently broke verification on opus.'),
    ('verify_max_tokens', '400',
     '200 was not enough to contain a thinking block AND an answer.')
ON CONFLICT (param) DO UPDATE SET value = EXCLUDED.value, rationale = EXCLUDED.rationale;
