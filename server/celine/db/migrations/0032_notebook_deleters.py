"""The notebook says who deleted what. From now on the agent's notebook__delete leaves
`.deleted-by-agent` in the `.trash/<time>/` folder it moves a note to, and the user's
delete leaves `.deleted-by-user` (holding the <time>) in the .versions of each note it
takes (skills/notebook.py trash): only a note so marked is the agent's to read in the
trash and restore, and the texts kept before the user's line are the user's.

The trash's older entries get the same marks here, once. A note is the agent's when a
notebook__delete of its path that ended ok was running at the folder's time; every other
entry, each folder too, is the user's, and draws its line in the .versions of the notes
it holds (the newest line of a note wins). Run again, it changes nothing."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

MARK = ".deleted-by-agent"
LINE = ".deleted-by-user"
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
    notebook = db_path.parent / "notebook"
    trash = notebook / ".trash"
    if not trash.is_dir():
        return
    calls = [(path, datetime.fromisoformat(start) - SLACK, datetime.fromisoformat(end) + SLACK)
             for path, start, end in conn.execute(
                 "SELECT coalesce(json_extract(display, '$.path'), "
                 "json_extract(arguments, '$.path')), started_at, finished_at FROM tool_calls "
                 "WHERE name = 'notebook__delete' AND state = 'ok' "
                 "AND started_at IS NOT NULL AND finished_at IS NOT NULL")]
    for d in sorted(trash.iterdir()):
        try:
            at = datetime.strptime(d.name, NAMED).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if not d.is_dir():
            continue
        path = _note(d)
        if path and any(p == path and start <= at <= end for p, start, end in calls):
            (d / MARK).touch()
        if (d / MARK).exists():
            continue
        for f in d.rglob("*"):
            kept = notebook / ".versions" / f.relative_to(d).as_posix()
            if f.is_file() and kept.is_dir():
                line = kept / LINE
                if not line.is_file() or line.read_text().strip() < d.name:
                    line.write_text(d.name)
