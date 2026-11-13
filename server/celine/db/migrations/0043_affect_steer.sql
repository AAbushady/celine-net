-- A reading keeps the pushes its request carried (affect.steer), [] when it was read only: a
-- reply steered toward tired reads more tired whatever the agent felt.
ALTER TABLE affect_readings ADD COLUMN steer TEXT NOT NULL DEFAULT '[]';
