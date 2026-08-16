-- ============================================================================
-- JPD 013 — artifacts belong to a NEED before they belong to a solution
--
-- `artifacts.solution_id` was NOT NULL (001), but Phases A/B/C work at the need
-- level: a `solutions` row is created when a need becomes a sellable product,
-- which is AFTER the forge has produced something to sell.
--
-- Cost of finding this the hard way: 696 seconds of paid generation completed
-- successfully and then failed to insert.
-- ============================================================================

ALTER TABLE artifacts ALTER COLUMN solution_id DROP NOT NULL;

-- An artifact must still belong to SOMETHING. Dropping NOT NULL without this
-- would allow an orphan artifact attached to nothing, which is the quieter
-- version of the same bug.
ALTER TABLE artifacts DROP CONSTRAINT IF EXISTS artifacts_belongs_to_something;
ALTER TABLE artifacts ADD CONSTRAINT artifacts_belongs_to_something
    CHECK (solution_id IS NOT NULL OR need_id IS NOT NULL);
