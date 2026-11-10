"""Whether a conversation is at work: two rules, each asked in one place.

Running: a turn of it executes now. A live turn (core/states.py LIVE) counts, but a
queued one that the hold keeps aside does not (hold.py: frozen, or paused): it has not
started, and its row says why. What a list shows as Running, a row's step, the
background list's `working` and the "also working now" of its prompt read this.

Busy: something will still come of it. A live turn of its own (a held one too: it will
run), a job of it that still runs, a message that wakes it and waits, a sub-agent of it
queued, or a sub-agent under it busy. What decides that a run is finished, a sub-agent
has reported, a mission is stuck or done with its leftovers, and who "everyone working"
is read this.

A list never asks Busy and a decision never asks Running: a chat whose turn ended while
its job goes on is not Running (nothing executes) and is Busy (its result still comes)."""
from __future__ import annotations

import sqlite3

from .core.states import LIVE_SQL
from .hold import held_sql


def queued_sql(conversation: str = "?") -> str:
    """A queued brief, over intercom i and intercom_deliveries d: landed in
    `conversation` (a placeholder or a column) without waking its sub-agent, and no turn
    answers it yet."""
    return (f"d.conversation_id = {conversation} AND i.kind='brief' AND i.wake=0 AND "
            "i.cancelled_at IS NULL AND d.state <> 'cancelled' AND d.turn_id IS NULL AND "
            "d.message_id IS NOT NULL")


# ---- Running -------------------------------------------------------------------------------

def running_sql(models: frozenset[str] | None, t: str = "t", c: str = "c") -> str:
    """SQL, true for a turn `t` of the conversation `c` that executes now: live, and not
    a queued one the hold keeps aside (`models`: hold.answering)."""
    return f"({t}.state IN {LIVE_SQL} AND NOT ({t}.state = 'queued' AND {held_sql(models, c)}))"


def family_running_sql(models: frozenset[str] | None, c: str = "c") -> str:
    """SQL, true when the conversation `c` or a sub-agent under it has a turn running: a
    list's Running group."""
    return ("EXISTS (WITH RECURSIVE fam(id) AS (SELECT " + c + ".id UNION ALL SELECT s.id "
            "FROM conversations s JOIN fam ON s.agent_of = fam.id) SELECT 1 FROM turns t "
            "JOIN fam ON fam.id = t.conversation_id JOIN conversations hc ON "
            "hc.id = t.conversation_id WHERE " + running_sql(models, "t", "hc") + ")")


def running(c: sqlite3.Connection, cid: str, models: frozenset[str] | None = None) -> bool:
    """A turn of conversation `cid` itself executes now."""
    return c.execute(
        "SELECT 1 FROM turns t JOIN conversations c ON c.id = t.conversation_id "
        f"WHERE t.conversation_id = ? AND {running_sql(models)} LIMIT 1", (cid,)).fetchone() is not None


# ---- Busy ----------------------------------------------------------------------------------

# SQL: the conversations with work of their own still to come (a live turn, a running
# job, a waking message that waits); a queued sub-agent and the sub-agents under one are
# `busy`'s to add
OWN_BUSY_SQL = (
    f"SELECT conversation_id FROM turns WHERE state IN {LIVE_SQL} "
    "UNION SELECT conversation_id FROM jobs WHERE state='running' "
    "UNION SELECT d.conversation_id FROM intercom_deliveries d JOIN intercom i ON i.id = "
    "d.intercom_id WHERE d.state='pending' AND i.wake=1 AND i.cancelled_at IS NULL")


def busy(c: sqlite3.Connection, cid: str, but: int | None = None) -> bool:
    """Something will still come of conversation `cid`: a live turn, a running job or a
    waking message waiting in it, a sub-agent of it queued, or a sub-agent under it busy.
    `but` is a message in `cid` that does not count (a mission's time limit)."""
    if c.execute(
        f"SELECT 1 FROM turns WHERE conversation_id=? AND state IN {LIVE_SQL} "
        "UNION ALL SELECT 1 FROM jobs WHERE conversation_id=? AND state='running' "
        "UNION ALL SELECT 1 FROM intercom_deliveries d JOIN intercom i ON i.id = "
        "d.intercom_id WHERE d.conversation_id=? AND d.state='pending' AND i.wake=1 "
        "AND i.cancelled_at IS NULL AND i.id IS NOT ? LIMIT 1",
        (cid, cid, cid, but)).fetchone():
        return True
    for (sub,) in c.execute("SELECT id FROM conversations WHERE agent_of=?", (cid,)).fetchall():
        if queued(c, sub) is not None or busy(c, sub):
            return True
    return False


def queued(c: sqlite3.Connection, sub: str) -> tuple[int, str] | None:
    """Sub-agent `sub`'s queued brief: (its intercom id, its message id), or None."""
    r = c.execute("SELECT i.id, d.message_id FROM intercom i JOIN intercom_deliveries d "
                  f"ON d.intercom_id = i.id WHERE {queued_sql()} ORDER BY i.id LIMIT 1",
                  (sub,)).fetchone()
    return (r[0], r[1]) if r else None
