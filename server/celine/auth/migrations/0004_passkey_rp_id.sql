-- Where a passkey works: the RP ID it was made for, the host of the address its browser
-- was on (localhost, or the app's address). A passkey from before has none until its next
-- login or proof, which stores the one it answered.
ALTER TABLE passkeys ADD COLUMN rp_id TEXT;
