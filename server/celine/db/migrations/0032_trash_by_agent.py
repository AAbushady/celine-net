"""The notebook's trash says who deleted each note. From now on the agent's notebook__delete
leaves `.deleted-by-agent` in the `.trash/<time>/` folder it moves a note to
(skills/notebook.py trash), and only a note so marked is the agent's to restore. A note it
moved there before gets the mark here, once: a notebook__delete of that path that ended ok
was running at the folder's time. The user's deletes and every folder stay unmarked. Run
again, it marks nothing new."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

MARK = ".deleted-by-agent"
NAMED = "%Y-%m-%d_%H%M%S_%f"        # a .trash folder's name: when the delete moved it, UTC
SLACK = timedelta(seconds=1)        # a call's times are stamped to the millisecond


def _note(d):
    """The path of the one note a .trash/<time> folder holds, None for a folder."""
    p, parts = d, []
    while p.is_dir() and len(kids := [k for k in p.iterdir() if k.name != MARK]) == 1:
        p = kids[0]
        parts.append(p.name)
    return "/".join(parts) if parts and p.is_file() else None


def up(conn, db_path) -> None:
    trash = db_path.parent / "notebook" / ".trash"
    if not trash.is_dir():
        return
    calls = [(path, datetime.fromisoformat(start) - SLACK, datetime.fromisoformat(end) + SLACK)
             for path, start, end in conn.execute(
                 "SELECT coalesce(json_extract(display, '$.path'), "
                 "json_extract(arguments, '$.path')), started_at, finished_at FROM tool_calls "
                 "WHERE name = 'notebook__delete' AND state = 'ok' "
                 "AND started_at IS NOT NULL AND finished_at IS NOT NULL")]
    for d in trash.iterdir():
        try:
            at = datetime.strptime(d.name, NAMED).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        path = _note(d) if d.is_dir() else None
        if path and any(p == path and start <= at <= end for p, start, end in calls):
            (d / MARK).touch()
