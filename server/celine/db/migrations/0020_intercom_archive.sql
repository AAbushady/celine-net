-- Threads the user moved out of the Intercom's lists (intercom/store.py archive): until a
-- message newer than `through` lands in the thread, or one in it starts to wait for them.
CREATE TABLE intercom_archive (
  thread      INTEGER PRIMARY KEY REFERENCES intercom(id),
  through     INTEGER NOT NULL,
  archived_at TEXT NOT NULL
);
