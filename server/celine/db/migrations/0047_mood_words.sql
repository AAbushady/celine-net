-- A feeling the agent named itself keeps its own words (mind__feel `feeling`), shown in place of
-- its word of the list, which stays the point its mood is worked out from. NULL: no words.
ALTER TABLE moods ADD COLUMN words TEXT;
