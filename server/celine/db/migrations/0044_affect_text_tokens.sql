-- A reading keeps every summary its server sent per probe (mean, last, text), and the tokens of
-- the reply's visible text that `text` covers: NULL when the server does not say.
ALTER TABLE affect_readings ADD COLUMN text_tokens INTEGER;
