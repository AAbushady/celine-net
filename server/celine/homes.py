"""Where a conversation lives in the app: the server's one answer, and where it sits
(`path`, its crumbs). Every list row, a sender on the Intercom and an event carry it; the client never
works it out.

A conversation's home follows its root, the top of its `agent_of` chain (both fixed at
creation, so the home is too): a mission's root is under Missions, a background root
(a run, what an event started) under Routines, anything else with it. A sub-agent opens
in its root's home: /mission/<id>, /routines/<day>/task/<id> or /chat/<id>. The day
in a Routines link is a calendar date in the app's zone: a run's is its row's `day`, what
an event started its own first day."""
from __future__ import annotations

import sqlite3

from .clock import day_of

# A mission's name as people and the agent read it, over missions `m` and its conversation
# `c`: the title the title role gave it while its own name is still the one made from its
# brief (title_auto), else the name it was started or renamed with. NULL where `m` is no
# mission (a LEFT JOIN). Every place that names a mission reads this, so the Missions
# page, a crumb, a sender's name, Waiting for you and an approval say the same.
MISSION_NAME = ("CASE WHEN m.conversation_id IS NULL THEN NULL "
                "WHEN c.title_auto AND coalesce(c.title, '') <> '' THEN c.title ELSE m.name END")


def mission_name_sql(m: str = "m", c: str = "c") -> str:
    """MISSION_NAME for other aliases of the two tables."""
    return MISSION_NAME.replace("m.", f"{m}.").replace("c.", f"{c}.")


def mission_name(c: sqlite3.Connection, cid: str) -> str | None:
    """Inside a read: the name of the mission of conversation `cid` as people read it
    (MISSION_NAME); None: it is no mission."""
    r = c.execute(f"SELECT {MISSION_NAME} FROM missions m JOIN conversations c "
                  "ON c.id = m.conversation_id WHERE m.conversation_id=?", (cid,)).fetchone()
    return r[0] if r else None
_COLS = "id, kind, trigger_key, created_at"


def chain(c: sqlite3.Connection, cid: str, cols: str = _COLS) -> list[sqlite3.Row]:
    """`cid` and the conversations above it on its agent_of chain, itself first and its
    top last (`cols` of each), a loop in the chain stopping the walk: the one walk up
    the chain. [] when there is no such conversation."""
    out: list[sqlite3.Row] = []
    seen: set[str] = set()
    while cid and cid not in seen:
        seen.add(cid)
        r = c.execute(f"SELECT {cols}, agent_of FROM conversations WHERE id=?",
                      (cid,)).fetchone()
        if r is None:
            break
        out.append(r)
        cid = r["agent_of"]
    return out


def root(c: sqlite3.Connection, cid: str) -> sqlite3.Row | None:
    """The top of `cid`'s agent_of chain (itself when it has no parent). None: no such
    conversation."""
    up = chain(c, cid)
    return up[-1] if up else None


def run_of(c: sqlite3.Connection, cid: str) -> sqlite3.Row | None:
    """The run `cid` works in: the run whose conversation is the top of its chain, so the
    run's own conversation and every sub-agent under it, at any depth. None: any other
    conversation."""
    top = root(c, cid)
    if top is None:
        return None
    return c.execute("SELECT id, schedule, label, state FROM runs WHERE conversation_id=?",
                     (top["id"],)).fetchone()


def run_link(day: str, run_id: str) -> str:
    """Where a schedule's run opens: under its `day`."""
    return f"/routines/{day}/run/{run_id}"


def of(c: sqlite3.Connection, cid: str) -> dict:
    """{home: it | missions | routines, link: the app path that shows it}; both None
    for a conversation that is not there (gone permanently), so a caller may index it."""
    top = root(c, cid)
    if top is None:
        return {"home": None, "link": None}
    if top["kind"] == "mission":
        return {"home": "missions", "link": f"/mission/{cid}"}
    if top["kind"] == "background":
        key = top["trigger_key"] or ""
        run = c.execute("SELECT id, day FROM runs WHERE id=?",
                        (key.split(":")[1],)).fetchone() if key.startswith("run:") else None
        day = run["day"] if run else day_of(top["created_at"])
        link = run_link(day, run["id"]) if run and top["id"] == cid \
            else f"/routines/{day}/task/{cid}"
        return {"home": "routines", "link": link}
    return {"home": "agent", "link": f"/chat/{cid}"}


def _named(c: sqlite3.Connection, r) -> dict:
    """One conversation of a chain as a crumb: {id, kind (chat, mission, run, background,
    agent), name, link}."""
    kind, name = r["kind"], None
    if kind == "mission":
        name = mission_name(c, r["id"])
    elif kind == "agent":
        name = r["agent_name"]
    elif (r["trigger_key"] or "").startswith("run:"):
        run = c.execute("SELECT label FROM runs WHERE id=?", (r["trigger_key"][4:],)).fetchone()
        kind, name = "run", run[0] if run else None
    return {"id": r["id"], "kind": kind, "name": name or r["title"] or "Untitled",
            "link": of(c, r["id"])["link"]}


def path(c: sqlite3.Connection, cid: str, _seen: frozenset[str] = frozenset()) -> list[dict]:
    """Where `cid` sits, from the top down to itself, each a crumb (_named): a mission
    started from another conversation first carries that one's path (a run, a chat), so
    the path of a run's mission's sub-agent is [run, mission, sub-agent]. [] when it is
    gone."""
    up = chain(c, cid, "id, kind, title, trigger_key, agent_name")
    if not up:
        return []
    top, origin = up[-1], []
    if top["kind"] == "mission" and top["id"] not in _seen:
        by = c.execute("SELECT started_by FROM missions WHERE conversation_id=?",
                       (top["id"],)).fetchone()
        if by and by[0]:
            origin = path(c, by[0], _seen | {x["id"] for x in up})
    return origin + [_named(c, r) for r in reversed(up)]
