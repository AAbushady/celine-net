-- A model request can ask for a whole checkpoint or a latent upscaler too (model_requests.kind
-- 'checkpoint', 'latent_upscaler'): the files an LTX workflow loads with ckpt_name and with
-- LatentUpscaleModelLoader. SQLite keeps a CHECK in the table, so the table is made again with
-- the new words and its rows copied as they are (as 0037 did).
CREATE TABLE model_requests_new (
  id              TEXT PRIMARY KEY,
  conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
  tool_call_id    TEXT REFERENCES tool_calls(id) ON DELETE SET NULL,
  provider        TEXT NOT NULL,
  preset          TEXT NOT NULL,
  kind            TEXT NOT NULL CHECK (kind IN ('lora','model','checkpoint','vae','text_encoder',
                                                 'latent_upscaler')),
  look            TEXT NOT NULL,
  saved_as        TEXT NOT NULL,
  why             TEXT NOT NULL,
  state           TEXT NOT NULL CHECK (state IN ('asked','denied','unanswered','fetching',
                                                 'listing','ready','failed')),
  job             TEXT,
  done            INTEGER,
  size            INTEGER,
  error           TEXT,
  created_at      TEXT NOT NULL,
  finished_at     TEXT
);
INSERT INTO model_requests_new (id, conversation_id, tool_call_id, provider, preset, kind, look,
                                saved_as, why, state, job, done, size, error, created_at,
                                finished_at)
  SELECT id, conversation_id, tool_call_id, provider, preset, kind, look, saved_as, why, state,
         job, done, size, error, created_at, finished_at
  FROM model_requests;
DROP TABLE model_requests;
ALTER TABLE model_requests_new RENAME TO model_requests;
CREATE INDEX model_requests_state ON model_requests(state, created_at);
