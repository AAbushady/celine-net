-- A model file or LoRA the agent asked for (comfyui/model_files.py): what it asked, the
-- helper's look at the file, where it goes and how the request stands. The comfy helper
-- keeps the file and its download; this row keeps the request, so a restart follows its
-- download again. A row outlives its conversation (the page lists what came by request).
CREATE TABLE model_requests (
  id              TEXT PRIMARY KEY,
  conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
  tool_call_id    TEXT REFERENCES tool_calls(id) ON DELETE SET NULL,
  provider        TEXT NOT NULL,
  preset          TEXT NOT NULL,
  kind            TEXT NOT NULL CHECK (kind IN ('lora','model')),
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
CREATE INDEX model_requests_state ON model_requests(state, created_at);
