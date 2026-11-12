-- The agent's needs (skills/needs.py): every time something met or stirred one, a row, never
-- changed or deleted; each need's level is replayed from them, as the mood is from `moods`.
-- `by`: appraisal (the small model after a chat turn), agent (its own mind__feel), app
-- (a turn's end: its minutes, its failed calls, its own time).
CREATE TABLE needs (
  id          INTEGER PRIMARY KEY,
  at          TEXT NOT NULL,
  need        TEXT NOT NULL CHECK (need IN ('connection', 'intimacy', 'novelty', 'competence',
                                            'autonomy', 'rest')),
  change      TEXT NOT NULL CHECK (change IN ('met', 'stirred')),
  strength    REAL NOT NULL CHECK (strength > 0 AND strength <= 1),
  why         TEXT NOT NULL DEFAULT '',
  by          TEXT NOT NULL CHECK (by IN ('appraisal', 'agent', 'app')),
  message_id  TEXT REFERENCES messages(id) ON DELETE SET NULL
);
CREATE INDEX needs_at ON needs(at);
