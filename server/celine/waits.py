"""Waiting for you: everything that needs the user, in one list (GET /api/waits,
api/waits.py; the rail's Waiting and its sheet). The server's one answer; the client counts nothing.

- every request of the agent's (approvals.py), as its card;
- every chat the agent started for the user that they have not written in yet
  (intercom__start_chat, conversations/store.opened), its first message as the line;
- every mission in Needs you or with a message of its own that waits for the user, once,
  that message or its note as the line (missions/watch.py); a bug report it filed is
  an Intercom thread;
- every chat source whose login was lost and is not back (kb/connectors.py).

An Intercom thread is news: the user reads it in the Intercom, and it is no item here.
`unread` counts the threads with something unread, for the Intercom's own rail item and
its Waiting for you. A freeze and a pause have their own rail items; runs, failed or
waiting, belong to Routines. `run` relays the events that change the list or that count
as one, `waits.changed`."""
from __future__ import annotations

import logging

import anyio

from .core.event_types import (
    APPROVALS_CHANGED,
    CONVERSATION_ARCHIVED,
    CONVERSATION_DELETED,
    CONVERSATION_RESTORED,
    CONVERSATION_UNARCHIVED,
    INTERCOM_STATE,
    MISSION_STATE,
    SOURCE_STATE,
    TURN_STATE,
    WAITS_CHANGED,
)

log = logging.getLogger("celine.waits")

CHANGES = frozenset({APPROVALS_CHANGED, MISSION_STATE, SOURCE_STATE, INTERCOM_STATE,
                     CONVERSATION_DELETED, CONVERSATION_RESTORED, CONVERSATION_ARCHIVED,
                     CONVERSATION_UNARCHIVED})
# a lost login's line, by platform; any other platform reads LOST
LOST_BY = {"whatsapp": "Not connected to WhatsApp. Log in with QR code in Settings, WhatsApp.",
           "discord": "Not connected to Discord. Log in again in Settings, Discord."}
LOST = "Not connected to {title}. Log in again in Settings, {title}."
LINE = 320          # characters of a message on its item (the sheet shows three lines)


def _line(text: str | None) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= LINE else s[:LINE - 1].rstrip() + "…"


def lost_logins(connectors) -> list[dict]:
    """The chat sources whose login was there and got lost, and is not back."""
    if connectors is None:
        return []
    return [{"kind": "login", "id": s["kb"], "title": s["title"],
             "line": LOST_BY.get(s["platform"]) or LOST.format(title=s["title"]),
             "link": f"/settings/sources/{s['kb']}"}
            for s in connectors.states()
            if s.get("login_lost_at") and (s.get("connection") or {}).get("state") == "needs_login"]


def _missions(c) -> list[dict]:
    """Every mission in Needs you or with a message to the user that waits (its home is
    the mission: intercom/store.py MISSION_FROM), once; the line is the newest waiting
    message, else its note."""
    from . import homes
    from .core.ids import now
    from .intercom.store import _UNREAD, BUG
    rows = c.execute(
        "SELECT * FROM (SELECT m.conversation_id, m.name, m.note, m.status, m.updated_at, "
        "(SELECT i.text FROM intercom i WHERE i.from_addr = 'agent:' || m.conversation_id "
        f"AND i.kind <> 'job' AND {_UNREAD} AND NOT {BUG} ORDER BY i.id DESC LIMIT 1) AS asked "
        "FROM missions m JOIN conversations cv ON cv.id = m.conversation_id "
        "WHERE cv.trashed_at IS NULL) WHERE status = 'needs_you' OR asked IS NOT NULL "
        "ORDER BY updated_at DESC", ("user", now())).fetchall()
    return [{"kind": "mission", "id": r["conversation_id"], "title": r["name"],
             "line": _line(r["asked"] or r["note"]),
             "link": homes.of(c, r["conversation_id"])["link"]} for r in rows]


def _chats(c) -> list[dict]:
    """Every chat the agent started for the user that they have not written in yet
    (conversations/store.opened), newest first; the line is its first message there."""
    from . import homes
    from .conversations.store import opened
    return [{"kind": "chat", "id": r["id"], "title": r["title"] or "Untitled",
             "line": _line(r["text"]), "link": homes.of(c, r["id"])["link"]}
            for r in opened(c)]


def unread(c) -> int:
    """How many Intercom threads hold something the user has not read: the threads the
    Intercom's Waiting for you lists (GET /api/intercom/threads?unread=true). The
    Intercom's rail item shows it; none of them is an item here."""
    from .intercom.store import threads
    return len(threads(c, "user", unread=True, received=True, archived=False, user_in=True))


# the head each kind is listed under in the sheet, the Notifications words
GROUPS = {"approval": "Approvals", "chat": "Chats", "mission": "Missions",
          "login": "Sources"}


async def items(services) -> list[dict]:
    """Every item that waits, each with its `group` (GROUPS): the requests first (oldest
    first), then the chats started for the user, missions and lost logins (newest
    first). A request is {kind: "approval", approval: its card}; every other
    {kind, id, title, line, link}."""
    a = getattr(services, "approvals", None)
    asks = [{"kind": "approval", "approval": card} for card in (a.view() if a else [])]
    rows = await services.store.read(lambda c: _chats(c) + _missions(c))
    out = [*asks, *rows, *lost_logins(getattr(services, "connectors", None))]
    return [{**i, "group": GROUPS[i["kind"]]} for i in out]


async def _answered(store, ev: dict) -> bool:
    """A turn queued in a chat the agent started for the user: they may have written in
    it, which takes it off the list."""
    if ev.get("type") != TURN_STATE or ev.get("state") != "queued" or not ev.get("conversation"):
        return False
    from .intercom.store import OPENING
    key = OPENING + str(ev["conversation"])
    return await store.read(lambda c: c.execute(
        "SELECT 1 FROM kv WHERE k = ?", (key,)).fetchone()) is not None


async def run(store, bus) -> None:
    """For as long as the app runs: each event that changes the list or the Intercom's
    count (CHANGES, and a turn queued in a chat started for the user: `_answered`) on the
    global feed is followed by one `waits.changed`. A chat the agent starts sends its own
    (intercom/skill.py start_chat). The bus drops a slow reader, so this one subscribes
    again."""
    while True:
        tx, rx = bus.subscribe("global")
        try:
            async with rx:
                async for ev in rx:
                    if ev.get("type") in CHANGES or await _answered(store, ev):
                        store.emit_global({"type": WAITS_CHANGED})
        except anyio.get_cancelled_exc_class():
            raise
        except Exception:          # a bug: logged, the relay goes on
            log.exception("waits relay failed", extra={"event": "waits.relay_failed"})
        finally:
            bus.unsubscribe("global", tx)
