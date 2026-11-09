"""Threads the user moved out of the Intercom's lists (intercom/store.py archive): until a
message newer than `through` lands in the thread, or one in it starts to wait for them.

This fork's own step 20 made the table before upstream's steps 20 to 28 came in, so a
database that ran it has the table and never ran upstream's 0020_pass_on_this.sql. Here
both end the same: the table is made where it is missing, and so is the `piece` column
of guard_passes. Run again, it changes nothing."""
from __future__ import annotations


def up(conn, db_path) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS intercom_archive ("
        "thread INTEGER PRIMARY KEY REFERENCES intercom(id), "
        "through INTEGER NOT NULL, "
        "archived_at TEXT NOT NULL)")
    if "piece" not in {r[1] for r in conn.execute("PRAGMA table_info(guard_passes)")}:
        conn.execute("ALTER TABLE guard_passes ADD COLUMN piece TEXT")
