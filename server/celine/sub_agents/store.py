"""Sub-agents as rows. A sub-agent is a conversation with a parent (conversations.agent_of),
so what is read here comes from the conversations, turns, tool calls and intercom tables:
`look` works out where one stands, `agents_of` lists them as the overview shows them,
`tree_of` gives everything under a top conversation, `drop_queued` cancels queued briefs.
The tool (skill.py), the routes (api.py) and the other packages read sub-agents here."""
from __future__ import annotations

import sqlite3

from .. import activity, homes
from ..conversations import store as conversations
from ..core.ids import now
from ..core.statuses import AGENT_ENDED, AgentState, MissionStatus
from ..skills import tool_rows
from . import report


async def reads_only(store, cid: str) -> bool:
    """A sub-agent started in read mode: only read-only tools are in its list, and a
    call to any other is refused."""
    row = await store.read(lambda c: c.execute(
        "SELECT agent_of, access FROM conversations WHERE id=?", (cid,)).fetchone())
    return row is not None and bool(row["agent_of"]) and row["access"] == "read"


def agents_of(c: sqlite3.Connection, parent: str | None = None, *,
              agent: str | None = None, own: dict) -> list[dict]:
    """Sub-agents, newest first, as the overview shows them: who, where, its `home` and
    `link` (homes.of: where it opens), working (busy: activity.py), the step at work,
    the latest report, its last words. Those of `parent`, the one `agent`, or (neither
    given) every one not in the archive. `own`: the MCP servers' row words (store.tool_words)."""
    if agent:
        where, args = "WHERE s.id = ? AND s.agent_of IS NOT NULL", (agent,)
    elif parent:
        where, args = "WHERE s.agent_of = ?", (parent,)
    else:
        where, args = "WHERE s.agent_of IS NOT NULL AND s.archived_at IS NULL", ()
    rows = c.execute(
        f"SELECT s.id, s.agent_of AS parent, s.agent_name AS name, s.access, "
        f"s.notebook_folder AS folder, s.model_id AS model, s.agent_cli AS cli, "
        f"s.agent_device AS device, s.agent_template AS template, "
        f"s.created_at, s.updated_at FROM conversations s "
        f"{where} ORDER BY s.created_at DESC, s.id DESC", args).fetchall()
    return [{**dict(r), **look(c, r["id"], own), **homes.of(c, r["id"])} for r in rows]


def labelled(settings, agents: list[dict]) -> list[dict]:
    """`agents` as a client gets them: each `model` its label (attachments.label), never
    its ref. Every route, snapshot and event that sends agents_of or tree_of rows."""
    from ..attachments import label
    return [{**a, "model": label(settings, a["model"]) or None} for a in agents]


def tree_of(c: sqlite3.Connection, cid: str, own: dict) -> dict | None:
    """Everything under the top of `cid`'s chain, each before its own and siblings
    oldest first: `agents`, its sub-agents, and `missions`, the missions it or they
    started (their sub-agents in `agents`, under them). Each: id, kind (agent, mission),
    parent, depth, name, state (an AgentState; a mission's MissionStatus), display (that
    state as the page shows it, its words and tone), reason (why it failed, stopped
    or paused; a mission's note), step_words, model (the newest that answered, else its
    own), what all its turns took (report.work) and link. `origin`: where the top came
    from (homes.path above it: the run that started a mission); `total`: tokens and
    seconds of the top and everything under it. None: no such conversation."""
    top = homes.root(c, cid)
    if top is None:
        return None
    out: list[dict] = []
    seen = {top["id"]}
    total = {"prompt_tokens": 0, "completion_tokens": 0, "seconds": 0}

    def add(w: dict) -> None:
        for k in total:
            total[k] += w[k]

    def node(r, parent: str, depth: int, kind: str, name: str,
             state: AgentState | MissionStatus, reason: str | None, step: str | None) -> None:
        w = report.work(c, r["id"], whole=True)
        add(w)
        out.append({"id": r["id"], "kind": kind, "parent": parent, "depth": depth,
                    "name": name, "state": state, "display": state.display,
                    "reason": reason, "step_words": step,
                    "model": (w["models"] or [r["model_id"]])[-1],
                    "prompt_tokens": w["prompt_tokens"],
                    "completion_tokens": w["completion_tokens"],
                    "seconds": w["seconds"], "link": homes.of(c, r["id"])["link"]})
        walk(r["id"], depth + 1)

    def walk(parent: str, depth: int) -> None:
        for r in c.execute("SELECT id, agent_name, model_id FROM conversations WHERE "
                           "agent_of=? ORDER BY created_at, id", (parent,)).fetchall():
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            f = look(c, r["id"], own)
            node(r, parent, depth, "agent", r["agent_name"], f["state"], f["reason"],
                 f["step_words"] if f["working"] else None)
        for r in c.execute(f"SELECT cv.id, cv.model_id, {homes.mission_name_sql('m', 'cv')} AS name, "
                           "m.status, m.note FROM "
                           "missions m JOIN conversations cv ON cv.id = m.conversation_id "
                           "WHERE m.started_by=? ORDER BY cv.created_at, cv.id",
                           (parent,)).fetchall():
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            status = MissionStatus(r["status"])
            node(r, parent, depth, "mission", r["name"], status,
                 _first_line(r["note"])
                 if status in (MissionStatus.NEEDS_YOU, MissionStatus.STOPPED) else None, None)
    add(report.work(c, top["id"], whole=True))
    walk(top["id"], 1)
    return {"top": top["id"], "origin": homes.path(c, top["id"])[:-1],
            "agents": [n for n in out if n["kind"] == "agent"],
            "missions": [n for n in out if n["kind"] == "mission"], "total": total}


def drop_queued(c: sqlite3.Connection, cid: str) -> None:
    """Cancel the queued briefs of `cid` and every sub-agent under it, so no release
    starts one."""
    for one in conversations.family(c, cid):
        c.execute("UPDATE intercom SET cancelled_at=? WHERE id IN (SELECT i.id FROM "
                  "intercom i JOIN intercom_deliveries d ON d.intercom_id = i.id "
                  f"WHERE {activity.queued_sql()})", (now(), one))


def look(c: sqlite3.Connection, sub: str, own: dict) -> dict:
    """What `read` shows of sub-agent `sub`: whether it is quiet (not busy: activity.py),
    its `state` (AgentState) with the `display` the page shows and the `reason` it
    failed, stopped or paused for, the tool its live turn runs (a drone's: the one opencode runs, drone.py), its latest
    report and its last words."""
    step = c.execute(
        "SELECT tc.name FROM tool_calls tc JOIN messages m ON m.id = tc.message_id "
        "WHERE m.conversation_id=? AND tc.state IN ('pending', 'running', 'detached') "
        "ORDER BY tc.started_at DESC LIMIT 1", (sub,)).fetchone()
    flown = c.execute("SELECT agent_step FROM conversations WHERE id=?", (sub,)).fetchone()
    rep = c.execute("SELECT id, text FROM intercom WHERE from_addr=? AND kind='report' "
                    "ORDER BY id DESC LIMIT 1", (f"agent:{sub}",)).fetchone()
    quiet = not activity.busy(c, sub)
    queued = activity.queued(c, sub) is not None
    # a queued one is working too: it has a brief to do (release)
    flown = flown[0] if flown else None
    last = c.execute("SELECT state, reason FROM turns WHERE conversation_id=? ORDER BY "
                     "created_at DESC LIMIT 1", (sub,)).fetchone()
    state = AgentState.QUEUED if queued else AgentState.WORKING if not quiet else \
        AGENT_ENDED.get(last["state"] if last else "", AgentState.DONE)
    top = homes.root(c, sub)
    return {"quiet": quiet, "queued": queued, "working": queued or not quiet,
            "state": state, "display": state.display,
            "reason": _first_line(last["reason"])
            if last and state in AGENT_ENDED.values() else None,
            "root": top["id"] if top is not None else None,
            "step": step["name"] if step else flown,
            "step_words": tool_rows.step(step["name"], own) if step else flown,
            "report": dict(rep) if rep else None,
            "last": report.last_answer(c, sub)}


def _first_line(text: str | None) -> str | None:
    s = (text or "").strip().split("\n")[0].strip()
    return s or None
