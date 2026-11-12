-- Video: a batch can make clips (media_batches.kind 'video') and a clip is an attachment
-- of its own kind ('video'). SQLite keeps a CHECK in the table, so both tables are made
-- again with the new word and their rows copied as they are (migrate.py runs this with
-- foreign keys off, so dropping the old ones deletes nothing, and checks them after).
CREATE TABLE media_batches_new (
  id              TEXT PRIMARY KEY,
  kind            TEXT NOT NULL CHECK (kind IN ('images','music','video')),
  conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
  tool_call_id    TEXT REFERENCES tool_calls(id) ON DELETE CASCADE,
  state           TEXT NOT NULL CHECK (state IN ('running','done','stopped')),
  items           TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  finished_at     TEXT
);
INSERT INTO media_batches_new (id, kind, conversation_id, tool_call_id, state, items,
                               created_at, finished_at)
  SELECT id, kind, conversation_id, tool_call_id, state, items, created_at, finished_at
  FROM media_batches;
DROP TABLE media_batches;
ALTER TABLE media_batches_new RENAME TO media_batches;
CREATE INDEX media_batches_conversation ON media_batches(conversation_id, created_at);
CREATE INDEX media_batches_kind         ON media_batches(kind, created_at);

CREATE TABLE attachments_new (
  id              TEXT PRIMARY KEY,
  message_id      TEXT REFERENCES messages(id) ON DELETE CASCADE,
  sha256          TEXT NOT NULL REFERENCES blobs(sha256),
  filename        TEXT NOT NULL,
  kind            TEXT NOT NULL CHECK (kind IN ('image','text','binary','audio','video')),
  state           TEXT NOT NULL CHECK (state IN ('uploading','ready','failed')),
  created_at      TEXT NOT NULL,
  description     TEXT,
  described_by    TEXT,
  described_for   TEXT,
  tool_call_id    TEXT REFERENCES tool_calls(id) ON DELETE CASCADE,
  batch_id        TEXT REFERENCES media_batches(id) ON DELETE CASCADE,
  box_path        TEXT,
  life            TEXT CHECK (life IN ('temporary','kept','cleared')),
  life_at         TEXT,
  folder_path     TEXT
);
INSERT INTO attachments_new (id, message_id, sha256, filename, kind, state, created_at,
                             description, described_by, described_for, tool_call_id,
                             batch_id, box_path, life, life_at, folder_path)
  SELECT id, message_id, sha256, filename, kind, state, created_at, description,
         described_by, described_for, tool_call_id, batch_id, box_path, life, life_at,
         folder_path
  FROM attachments;
DROP TABLE attachments;
ALTER TABLE attachments_new RENAME TO attachments;
CREATE INDEX attachments_batch     ON attachments(batch_id);
CREATE INDEX attachments_message   ON attachments(message_id);
CREATE INDEX attachments_state     ON attachments(state, created_at);
CREATE INDEX attachments_tool_call ON attachments(tool_call_id);
CREATE INDEX attachments_life      ON attachments(life, life_at);
