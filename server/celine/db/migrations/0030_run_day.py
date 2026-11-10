"""A run's `day`: the calendar date of its slot in the app's zone, the day the Routines page
and routines__list give it under. Every reader worked it out from the slot until now; the
run is written with it from here on (schedules/runs.py `_insert`).

The column is added and filled from each run's slot, in the zone General settings names
(settings/general.toml beside celine.db; UTC when it names none that exists, as
clock.zone does). Nothing else in the table changes. Run again, it writes nothing."""
from __future__ import annotations

import tomllib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _zone(db_path: Path) -> ZoneInfo:
    try:
        general = tomllib.loads((db_path.parent / "settings" / "general.toml")
                                .read_text(encoding="utf-8"))
        return ZoneInfo(str(general.get("timezone") or "UTC"))
    except (OSError, tomllib.TOMLDecodeError, ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def up(conn, db_path) -> None:
    tz = _zone(Path(db_path))
    conn.execute("BEGIN")
    try:
        if "day" not in {r[1] for r in conn.execute("PRAGMA table_info(runs)")}:
            conn.execute("ALTER TABLE runs ADD COLUMN day TEXT")
        for rid, slot in conn.execute("SELECT id, slot FROM runs WHERE day IS NULL").fetchall():
            day = datetime.fromisoformat(slot).astimezone(tz).date().isoformat()
            conn.execute("UPDATE runs SET day = ? WHERE id = ?", (day, rid))
        conn.execute("CREATE INDEX IF NOT EXISTS runs_day ON runs(day)")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
