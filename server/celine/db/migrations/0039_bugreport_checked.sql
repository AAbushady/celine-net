-- A bug report says what the agent ran or read to confirm the problem before filing it
-- (bugreports.checked); the reports filed before it have none.
ALTER TABLE bugreports ADD COLUMN checked TEXT NOT NULL DEFAULT '';
