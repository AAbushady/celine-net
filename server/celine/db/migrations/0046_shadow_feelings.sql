-- What the appraiser read into the agent's own turns in shadow (skills/mind_shadow.py), kept
-- for it to compare, moving neither its mood nor its needs. One row per appraised turn,
-- also when the appraiser named nothing. No link to the conversation's row: a reading
-- stays when its conversation is deleted.
CREATE TABLE shadow_feelings (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  at              TEXT NOT NULL,
  conversation_id TEXT,
  turn_id         TEXT,
  kind            TEXT NOT NULL,    -- own time, routine, trigger, mission, chat
  emotion         TEXT,             -- NULL: it named no feeling
  strength        REAL,
  why             TEXT NOT NULL DEFAULT '',
  needs           TEXT NOT NULL DEFAULT '[]',   -- [{need, change, strength}]
  named           TEXT,             -- what the agent named with mind__feel in that turn
  read_chars      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX shadow_feelings_at ON shadow_feelings(at);
