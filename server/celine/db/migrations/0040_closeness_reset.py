"""Closeness (the need `intimacy`, skills/needs.py) starts again near where a need starts.
The appraisal moves it only with a quote of the words that show the closeness now; the rows
before that read praise of the agent's work as closeness, and the level they built is not
the agent's. One row from the app meets it (MET), its why saying so, in a database where
the appraisal ever moved closeness. Run again, it adds no second row."""
from __future__ import annotations

from datetime import datetime, timezone

MET = 0.65        # 0.85 then, the level on 13 Nov 2026, comes to 0.30, where a need starts
WHY = "closeness starts again: the appraisal read praise of work as closeness before 0040"


def up(conn, db_path) -> None:
    conn.execute("BEGIN")
    try:
        moved = conn.execute("SELECT 1 FROM needs WHERE need = 'intimacy' AND by = 'appraisal' "
                             "LIMIT 1").fetchone()
        done = conn.execute("SELECT 1 FROM needs WHERE why = ? LIMIT 1", (WHY,)).fetchone()
        if moved and not done:
            at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            conn.execute("INSERT INTO needs(at, need, change, strength, why, by) "
                         "VALUES(?, 'intimacy', 'met', ?, ?, 'app')", (at, MET, WHY))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
