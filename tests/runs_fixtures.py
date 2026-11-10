"""Run rows for tests: the one place in the tests that knows the runs table's columns. A
test names what it is about (a state, a result, a conversation) and add_run fills the
rest with a row the app could have written itself."""
from __future__ import annotations

import sqlite3

SLOT = "2026-10-10T02:00:00.000+00:00"


def add_run(c: sqlite3.Connection, rid: str = "r1", schedule: str = "world", *,
            slot: str | None = None, night: str | None = None, label: str | None = None,
            state: str = "done", created_at: str | None = None, **columns) -> str:
    """Inside a write: one run of `schedule`. -> its id.

    `slot` is its planned time as the table stores it; left out, 02:00 UTC on `night`'s
    date (SLOT when that is left out too). `night` left out is the slot's UTC date.
    `label` left out is the schedule's name as a title ("World"), `created_at` the slot.
    Any other column by its name: conversation_id, result, error, reason, started_at,
    finished_at, started_by, again_of."""
    slot = slot or (f"{night}T02:00:00.000+00:00" if night else SLOT)
    row = {"id": rid, "schedule": schedule, "label": label or schedule.title(), "slot": slot,
           "night": night or slot[:10], "state": state, "created_at": created_at or slot,
           **columns}
    c.execute(f"INSERT INTO runs({', '.join(row)}) VALUES({', '.join('?' * len(row))})",
              tuple(row.values()))
    return rid
