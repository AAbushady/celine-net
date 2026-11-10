"""A mission's report says its facts and its heading in its data: {mission, status, heading,
note}. The page read them out of the report's sentence until now ("Mission Night needs
you: which flat?"), which it could not do for a report with no note or a stopped mission.

Every report of a mission written before gets them once, from that sentence: the status
it names, the heading in the words the app shows for it, and the note after it (with the
lines under it). A sentence this does not read keeps its first line as the heading. Run
again, it writes nothing."""
from __future__ import annotations

import json
import re

SAID = re.compile(r"^(?P<mission>Mission .+?) (?P<said>needs you|done|stopped by .+?|stopped)"
                  r"(?:: (?P<note>.*)|\.)$")
# the status a sentence names, and the words the app shows for it (core/words.py MISSION, as
# they were when this step was written)
STATUS = {"needs you": ("needs_you", "waiting for you"), "done": ("done", "done"),
          "stopped": ("stopped", "stopped")}


def facts(text: str) -> dict:
    """{status, heading, note} of a mission report's text."""
    first, _, rest = (text or "").strip().partition("\n")
    m = SAID.match(first.strip())
    if m is None:
        return {"status": None, "heading": first.strip().rstrip("."), "note": rest.strip()}
    status, words = STATUS["stopped" if m["said"].startswith("stopped") else m["said"]]
    note = "\n".join(x for x in (m["note"] or "", rest) if x).strip()
    return {"status": status, "heading": f"{m['mission']} is {words}", "note": note}


def up(conn, db_path) -> None:
    conn.execute("BEGIN")
    try:
        rows = conn.execute("SELECT id, text, data FROM intercom WHERE kind = 'report' "
                            "AND data LIKE '%\"mission\"%'").fetchall()
        for iid, text, raw in rows:
            data = json.loads(raw or "{}")
            if not data.get("mission") or "heading" in data:
                continue
            conn.execute("UPDATE intercom SET data = ? WHERE id = ?",
                         (json.dumps({**data, **facts(text)}, ensure_ascii=False), iid))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
