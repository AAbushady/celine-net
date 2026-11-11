"""The intercom over HTTP: send as the user (or as `system:<name>` with a named token),
list messages and threads, mark the user's read, cancel a scheduled one, and the stream
of new messages (server-sent events, replayed from the table). A change also reaches the
pages as `intercom.state` on the global feed. The named tokens themselves are made and
revoked under /api/tokens, with the app session only."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..api.cursor import page_cursor, read_id
from ..api.deps import person_only, proven
from ..core.route_params import TheConfig, TheServices, TheStore
from ..core.ids import iso, parse_when
from ..errors import CelineError, NotFound
from ..clock import zone
from ..conversations import store as conversations
from . import store as rows
from . import tokens
from .render import header
from ..core.event_types import INTERCOM_STATE

router = APIRouter(prefix="/api")


class MessageIn(BaseModel):
    to: str
    text: str = ""
    subject: str | None = None
    reply_to: int | None = None
    in_: str | None = Field(None, alias="in")
    at: str | None = None
    until: str | None = None


async def _sender(request: Request, store) -> str:
    """`system:<name>` with a named token, else the user (the app session)."""
    token = tokens.bearer(request.scope.get("headers") or [])
    if token is None:
        return "user"
    name = await store.read(lambda c: tokens.name_of(c, token))
    if name is None:
        raise CelineError("not_found", request.url.path, 404)
    return f"system:{name}"


READ_ONLY = ("The Intercom is for reading, but for your replies to Bastet and your "
             "reminders: to talk about this, continue it in a chat.")


def _user_may_send(c, body: MessageIn, later: bool) -> bool:
    """What the user still sends here: a reply in a thread with Bastet (her decisions,
    guard/consult.py) and a reminder into a conversation (lib/reminders.ts: `in` or `at`,
    to `agent:<id>`). Everything else they say in a chat."""
    if body.reply_to is not None:
        return c.execute("SELECT 1 FROM intercom WHERE thread = (SELECT thread FROM intercom "
                         "WHERE id=?) AND (from_addr = ? OR to_addr LIKE ?) LIMIT 1",
                         (body.reply_to, rows.GUARD, f"%{rows.GUARD}%")).fetchone() is not None
    words = [w.strip() for w in body.to.split(",") if w.strip()]
    return later and bool(words) and all(w.startswith("agent:") for w in words)


@router.post("/intercom")
async def send(request: Request, store: TheStore, cfg: TheConfig, services: TheServices, body: MessageIn) -> dict:
    """From `system:<name>` with a named token, or from the user what READ_ONLY names
    (_user_may_send). `in` ("30 min") or `at` ("08:00") make it land later; `until`
    ("05:00", "2 h") to `all` is an announcement that stands until then."""
    sender = await _sender(request, store)
    text = body.text.strip()
    if not text:
        raise CelineError("bad_request", "Text is required.", 400)
    if sender == "user" and not await store.read(
            lambda c: _user_may_send(c, body, bool(body.in_ or body.at))):
        raise CelineError("bad_request", READ_ONLY, 400)
    tz, now_ = zone(cfg), datetime.now(timezone.utc)
    due = until_at = None
    if body.in_ or body.at:
        due = parse_when(body.in_, body.at, tz, now_)
        if due is None:
            raise CelineError("bad_request", 'When is like "30 min" or "08:00".', 400)
    if body.until:
        until_at = parse_when(body.until, body.until, tz, now_)
        if until_at is None:
            raise CelineError("bad_request", 'until is like "05:00" or "2 h".', 400)
    try:
        out = await services.intercom.send(
            sender, body.to, text, subject=body.subject, reply_to=body.reply_to,
            due_at=iso(due) if due else None,
            until_at=iso(until_at) if until_at else None)
    except KeyError:
        raise NotFound("chat or message") from None
    except ValueError as e:
        raise CelineError("bad_request", str(e), 400) from None
    if sender == "user" and body.reply_to is not None and due is None and out:
        from ..guard import consult           # their answer to Bastet, in her thread
        await consult.from_session(request, services, out["intercom_id"], text)
    return out


@router.get("/intercom")
async def list_messages(store: TheStore, cfg: TheConfig, to: str | None = None,
                        from_: str | None = Query(None, alias="from"),
                        thread: int | None = None, scheduled: bool = False,
                        waiting: bool = False,
                        after: int = 0, limit: int = Query(200, ge=1, le=1000)) -> dict:
    """Messages to `to` and from `from` (stored addresses), in `thread`, above id
    `after`, oldest first, each with its state (pending, sent, cancelled) and whether it
    waits for the user (`unread`) and where it came from (`source`); `scheduled`: only
    those with a due time; `waiting`: only those that wait for the user (a mission's
    page: what it asked). One with a due time also has `at`: that time in the app's zone
    as the When of a reminder takes it ("2026-01-10 08:00"), so an edit that leaves it
    alone keeps its day and hour. With `thread`, also where the thread comes from (`source`) and
    whether it is archived (`archived`); the bug report it is: GET /api/bugreports?thread=."""
    def fn(c):
        out = {"messages": rows.listed(c, to=to, from_addr=from_, scheduled=scheduled,
                                       thread=thread, after=after, limit=limit,
                                       waiting=waiting)}
        if thread is not None:
            out["source"] = rows.thread_source(c, thread)
            out["archived"] = rows.is_archived(c, thread)
        for m in out["messages"]:
            if m.get("due_at"):
                tz = zone(cfg)
                m["at"] = datetime.fromisoformat(m["due_at"]).astimezone(tz).strftime(
                    "%Y-%m-%d %H:%M")
        return out

    return await store.read(fn)


class ThreadOut(BaseModel):
    thread: int
    name: str                       # who it is with, by name (everyone in it but you)
    subject: str | None
    source: dict | None             # where it comes from: {kind, title, link}
    count: int
    unread: int                     # messages in it that wait for you
    last: dict                      # the newest message, as GET /intercom gives it


class GroupOut(BaseModel):
    name: str                       # who these threads are with, by name
    addresses: list[str]            # the same, as stored addresses
    threads: int
    waiting: int                    # threads with a message that waits for you
    items: list[ThreadOut]          # the newest few (with `with`: up to `limit`)
    more: int                       # threads not in items


class ThreadsPage(BaseModel):
    groups: list[GroupOut]
    next: str | None


PER_GROUP = 3


@router.get("/intercom/threads")
async def threads(store: TheStore, with_: str = Query("user", alias="with"),
                  kind: Literal[rows.KINDS] | None = None,  # type: ignore[valid-type]
                  unread: bool = False, archived: bool = False,
                  limit: int = Query(50, ge=1, le=200),
                  cursor: str | None = None) -> ThreadsPage:
    """Threads grouped by who they are with, the group with the newest activity first,
    each group with its newest PER_GROUP threads and a count of the rest. Only threads you
    are in (store.WITH_USER), but for Bastet's. `with`: `user` (default) for the threads
    where something came to you, `all` for every thread you are in, `guard` for Bastet's
    threads (the Guard tab; Waiting for you never has them), else a name, stored address
    or conversation id: every thread with them that you are in, up to `limit` per group.
    `kind`: only messages of that kind. `archived`: only the threads you archived, up to
    `limit` per group; without it none of them.
    Your plain chat messages are never threads. `limit` groups a page; `next` is the
    following page's `cursor`."""
    addr = "user" if with_ == "user" else None
    who = None if with_ in ("user", "all") else rows.GUARD if with_ == "guard" else with_
    before = read_id(cursor)

    def fn(c):
        found = rows.threads(c, addr, unread, reader="user", received=addr == "user",
                             kind=kind, with_=who, archived=archived,
                             user_in=who != rows.GUARD)
        groups, nxt = rows.grouped(c, found, limit if who or archived else PER_GROUP, limit,
                                   before)
        return {"groups": groups, "next": page_cursor((nxt,)) if nxt else None}

    return ThreadsPage(**await store.read(fn))


CARRIED = "{user} continued Intercom thread #{thread} in this chat. The thread:"


@router.post("/intercom/threads/{tid}/conversations", dependencies=[Depends(person_only)])
async def to_chat(services: TheServices, tid: int) -> dict:
    """Continue in a chat: a new chat with it that carries thread `tid`, its messages
    landing there as one notice from the app without waking it (CARRIED, each message
    with the line it reads above it). The thread moves: what waits for you in it is read
    and it is archived, until something newer lands in it. -> {conversation_id}; the
    chat's `conversation.created` is on the feed."""
    store, cfg = services.store, services.cfg
    msgs = await store.read(lambda c: rows.thread(c, tid))
    if not msgs:
        raise NotFound("thread")
    tz, lines = zone(cfg), [CARRIED.format(user=cfg.user, thread=tid)]
    for m in msgs:
        if not m["cancelled"]:
            # its own conversations' lines are its own: "From you"
            mine = m["from"].startswith("agent:") and m["from_name"] == rows.NAMES["agent"]
            head = header({**m, "mine": mine}, cfg.user, tz) or \
                f"[From {cfg.user} (#{m['id']})]\n"     # their own first line, as typed
            lines.append(head + m["text"])
    cid = await conversations.create(store, (msgs[0]["subject"] or "").strip()[:200])
    await services.intercom.send("system:app", f"agent:{cid}", "\n\n".join(lines),
                                 kind="notice", wake=False)
    await _read(store, lambda c: [m["id"] for m in msgs])

    def shelve(c):
        if rows.archive(c, [tid]):
            store.global_event(c, INTERCOM_STATE, {"intercom_id": tid, "conversation_id": None})
    await store.write(shelve)
    return {"conversation_id": cid}


class ReadIn(BaseModel):
    ids: list[int]


async def _read(store, ids) -> list[int]:
    """The user's deliveries of `ids(c)` become read, each one an `intercom.state`."""
    def fn(c):
        done = rows.read(c, ids(c))
        for iid in done:
            store.global_event(c, INTERCOM_STATE, {"intercom_id": iid,
                                                     "conversation_id": None})
        return done

    return await store.write(fn)


@router.post("/intercom/read", dependencies=[Depends(person_only)])
async def read(store: TheStore, body: ReadIn | None = None, all: bool = False) -> dict:
    """The user's deliveries of these messages (`{ids}`) become read; `?all=1`: everything
    that waits for the user (Dismiss all)."""
    if all:
        return {"read": await _read(store, lambda c: rows.waiting_ids(c, "user"))}
    ids = body.ids if body is not None else []
    return {"read": await _read(store, lambda c: ids)}


class ThreadsIn(BaseModel):
    threads: list[int]


@router.post("/intercom/threads/archive", dependencies=[Depends(person_only)])
async def archive(store: TheStore, body: ThreadsIn | None = None, read: bool = False) -> dict:
    """These threads (`{threads}`) leave the lists until something newer lands in them; what
    waits for you in them is read. `?read=1`: what Archive read takes (`store.archivable`).
    -> {archived}."""
    asked = body.threads if body is not None else []

    def fn(c):
        done = rows.archive(c, rows.archivable(c) if read else asked)
        marks = ",".join("?" * len(done))
        for iid in rows.read(c, [r[0] for r in c.execute(
                f"SELECT id FROM intercom WHERE thread IN ({marks})", done)]):
            store.global_event(c, INTERCOM_STATE, {"intercom_id": iid, "conversation_id": None})
        if done:
            store.global_event(c, INTERCOM_STATE, {"intercom_id": done[-1],
                                                   "conversation_id": None})
        return done

    return {"archived": await store.write(fn)}


@router.post("/intercom/threads/restore", dependencies=[Depends(person_only)])
async def restore(store: TheStore, body: ThreadsIn) -> dict:
    """These threads back in the lists. -> {restored}."""

    def fn(c):
        done = rows.restore(c, body.threads)
        if done:
            store.global_event(c, INTERCOM_STATE, {"intercom_id": done[-1],
                                                   "conversation_id": None})
        return done

    return {"restored": await store.write(fn)}


KEEP_OPEN = 15.0        # seconds between comment lines, so proxies keep the stream


@router.get("/intercom/stream")
async def stream(request: Request, store: TheStore, services: TheServices, to: str | None = None, after: int = 0) -> StreamingResponse:
    """Every message above `after` (or the `Last-Event-ID`), then each new one as it is
    sent. `id:` is the message id. Without `to`: every message."""
    last = request.headers.get("last-event-id")
    after = int(last) if last and last.isdigit() else after
    intercom = services.intercom

    async def check():
        found = await store.read(lambda c: rows.listed(c, to=to, after=after, limit=200))
        return found or None

    async def events():
        nonlocal after
        while True:
            found = await intercom.wait_for(check, KEEP_OPEN)
            if not found:
                yield ": open\n\n"
                continue
            for m in found:
                after = m["id"]
                yield f"id: {m['id']}\ndata: {json.dumps(m, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.post("/intercom/{iid}/stop")
async def cancel(request: Request, store: TheStore, services: TheServices, iid: int) -> dict:
    sender = await _sender(request, store)
    if sender != "user":                # a token takes back only what it sent
        frm = await store.read(lambda c: c.execute(
            "SELECT from_addr FROM intercom WHERE id=?", (iid,)).fetchone())
        if frm is None or frm[0] != sender:
            raise CelineError("not_found", "This message was already delivered or cancelled.", 404)
    if not await services.intercom.cancel(iid):
        raise CelineError("not_found", "This message was already delivered or cancelled.", 404)
    return {"ok": True}


# ---- named tokens (Settings, Security) --------------------------------------------------

@router.get("/tokens")
async def list_tokens(store: TheStore) -> dict:
    return {"tokens": await store.read(tokens.listed)}


class TokenIn(BaseModel):
    name: str


@router.post("/tokens", dependencies=[Depends(proven)])
async def make_token(store: TheStore, body: TokenIn) -> dict:
    """The token is in this answer only; making one needs a fresh proof (api/deps.py)."""
    try:
        token = await store.write(lambda c: tokens.make(c, body.name.strip()))
    except ValueError as e:
        raise CelineError("bad_request", str(e), 400) from None
    return {"name": body.name.strip(), "token": token}


@router.post("/tokens/{tid}/revoke")
async def revoke_token(store: TheStore, tid: int) -> dict:
    if not await store.write(lambda c: tokens.revoke(c, tid)):
        raise NotFound("token")
    return {"ok": True}
