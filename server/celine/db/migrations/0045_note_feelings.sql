-- The feelings the agent kept with what it wrote in its notebook (skills/note_feelings.py):
-- one per section text, by the hash of that text, so a section that changes no longer shows
-- the feeling of the text it replaced. No link to the conversation's row: a feeling stays
-- with the note when its chat is deleted.
CREATE TABLE note_feelings (
  hash            TEXT PRIMARY KEY,   -- the section's text without the note's head lines
  path            TEXT NOT NULL,      -- the note it was written in, then
  felt            TEXT NOT NULL,      -- its own words
  at              TEXT NOT NULL,
  conversation_id TEXT
);
