-- What the agent's own replies read on the feeling probes (affect.py): one row per reply message from a
-- provider with Feeling probes on, the readings as its server sent them.
CREATE TABLE affect_readings (
  message_id      TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  turn_id         TEXT,
  at              TEXT NOT NULL,
  model           TEXT NOT NULL DEFAULT '',
  tokens          INTEGER NOT NULL DEFAULT 0,
  readings        TEXT NOT NULL      -- {probe: {"mean": x, "last": x} or null}
);
CREATE INDEX affect_readings_at ON affect_readings(at);
