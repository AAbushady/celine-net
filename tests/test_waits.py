"""Waiting for you (waits.py): one list of everything that needs the user, the
server's: a request as its card, a chat the agent started for them, a mission in Needs
you with its note, a lost login with its sentence; never a run, and never an Intercom
thread, which is news and counted for the Intercom alone. `waits.changed` follows every
event that changes it."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import anyio
import pytest
from chat_fixtures import until, user_says

from celine import approvals as ap, waits
from celine.conversations import store as conversations, tree
from celine.core.bus import EventBus
from celine.core.store import Store
from celine.guard.owner import _SEAL, Owner
from celine.intercom import Intercom
from celine.intercom import store as rows
from celine.missions import store as missions
from runs_fixtures import add_run

pytestmark = pytest.mark.anyio


class Connectors:
    def states(self):
        return [{"kb": "wa", "platform": "whatsapp", "title": "WhatsApp",
                 "connection": {"state": "needs_login"}, "login_lost_at": 1},
                {"kb": "dc", "platform": "discord", "title": "Discord",
                 "connection": {"state": "needs_login"}, "login_lost_at": None}]


async def _started(store, title: str, text: str) -> str:
    """A chat the agent started for the user, as intercom__start_chat leaves it."""
    cid = await conversations.create(store, title)

    def first(c):
        c.execute("INSERT INTO kv(k, v) VALUES(?, ?)", (rows.OPENING + cid, "where it came from"))
        tree.append(c, store, cid, None, "assistant", text, "model")
    await store.write(first)
    return cid


async def test_an_ask_a_started_chat_a_mission_and_a_lost_login_are_four_items(store):
    """The sheet listed every unread Intercom thread a second time, beside the Intercom's
    own Waiting for you, and left out the chats started for the user."""
    a = ap.Approvals(SimpleNamespace(user="sam", name="Celine"), store=store)
    chat = await conversations.create(store, "Plans")
    asked = await _started(store, "Invoice due", "It is due tomorrow.\nPay it?")
    night = await conversations.create(store, "Night", kind="mission")
    await missions.create(store, night, {"name": "Night", "terminal": "n", "brief": "b"})
    await store.write(lambda c: missions.set_status_sync(
        c, store, night, "needs_you", "A question for you at step 3: Book the hotel."))
    run = await conversations.for_trigger(store, "run:r1", "Radar")
    await store.write(lambda c: add_run(c, "r1", "radar", night="2026-10-22", state="failed",
                                        reason="x", conversation_id=run,
                                        created_at="2026-10-22T23:00:00+00:00"))
    await Intercom(store).send(f"agent:{chat}", "user", "I found three flats.\nmore")
    task = asyncio.create_task(a.ask("agent", chat, "change the Persona"))
    await until(lambda: a.waiting)
    services = SimpleNamespace(store=store, approvals=a, connectors=Connectors())
    got = await waits.items(services)
    assert [i["kind"] for i in got] == ["approval", "chat", "mission", "login"]
    assert [i["group"] for i in got] == ["Approvals", "Chats", "Missions", "Sources"]
    assert got[0]["approval"]["title"] == "Celine asks to change the Persona."
    assert got[0]["approval"]["where"] == "Chat Plans"
    assert got[1] == {"kind": "chat", "id": asked, "title": "Invoice due", "group": "Chats",
                      "line": "It is due tomorrow. Pay it?", "link": f"/chat/{asked}"}
    assert {k: got[2][k] for k in ("title", "line", "link")} == {
        "title": "Night", "line": "A question for you at step 3: Book the hotel.",
        "link": f"/mission/{night}"}
    assert got[3] == {"kind": "login", "id": "wa", "title": "WhatsApp", "group": "Sources",
                      "line": waits.LOST_BY["whatsapp"], "link": "/settings/sources/wa"}
    assert await store.read(waits.unread) == 1, "the thread is the Intercom's, counted there"
    await user_says(store, asked, "paid it")
    assert [i["kind"] for i in await waits.items(services)] == ["approval", "mission", "login"], \
        "a chat they wrote in waits no more"
    [aid] = a.waiting
    a.decide(aid, True, Owner("test", _SEAL))
    await task


async def test_a_bug_report_a_mission_filed_is_its_own_thread_in_the_intercom(store):
    from celine.bugreports import store as bugs
    night = await conversations.create(store, "Night", kind="mission")
    await missions.create(store, night, {"name": "Night", "terminal": "n", "brief": "b"})
    report = await store.write(lambda c: bugs.file(
        c, store, night, "Writes come back short", "high", "A write lost its end.", "", ""))
    got = await waits.items(SimpleNamespace(store=store, approvals=None, connectors=None))
    assert got == [], "a thread is no item of the list"
    [t] = await store.read(lambda c: rows.threads(c, "user", unread=True, received=True))
    assert t["thread"] == report["thread"] and await store.read(waits.unread) == 1
    assert await store.read(lambda c: rows.listed(c, from_addr=f"agent:{night}", waiting=True)) == []
    assert await store.write(lambda c: rows.read_from(c, f"agent:{night}")) == []


async def test_a_mission_that_wrote_to_the_user_waits_in_every_list(store):
    """Waiting for you, the chat list's row and the Missions list's `asked` read one set
    (intercom/store.py missions_asking): an active mission with a message that waits."""
    night = await conversations.create(store, "Night", kind="mission")
    await missions.create(store, night, {"name": "Night", "terminal": "n", "brief": "b"})
    assert await store.read(rows.missions_asking) == frozenset()
    await Intercom(store).send(f"agent:{night}", "user", "Which of the two flats?")
    assert await store.read(rows.missions_asking) == {night}
    got = await waits.items(SimpleNamespace(store=store, approvals=None, connectors=None))
    assert [(i["kind"], i["id"]) for i in got] == [("mission", night)]
    listed = {r["id"]: r["display"] for r in await conversations.list(store)}
    assert listed[night] == {"tone": "warn", "words": "Waiting for you"}, "the chat list says so too"
    await store.write(lambda c: rows.read_from(c, f"agent:{night}"))
    assert await store.read(rows.missions_asking) == frozenset(), "read: it waits no more"


async def test_nothing_waiting_is_an_empty_list(store):
    services = SimpleNamespace(store=store, approvals=None, connectors=None)
    assert await waits.items(services) == []


async def test_waits_changed_follows_each_event_that_changes_the_list(db_path):
    bus = EventBus()
    async with anyio.create_task_group() as tg:
        store = Store(db_path, bus=bus)
        await store.start(tg)
        tg.start_soon(waits.run, store, bus)
        await until(lambda: bus._subs["global"])
        seen: list[str] = []
        tx, rx = bus.subscribe("global")

        async def watch():
            async for ev in rx:
                seen.append(ev["type"])
        tg.start_soon(watch)
        store.emit_global({"type": "approvals.changed"})
        store.emit_global({"type": "kb.changed"})
        await until(lambda: "waits.changed" in seen)
        assert seen.count("waits.changed") == 1
        # a turn queued in a chat started for the user: they may have answered it
        await store.write(lambda c: c.execute(
            "INSERT INTO kv(k, v) VALUES(?, ?)", ("chat_opening:c1", "where it came from")))
        store.emit_global({"type": "turn.state", "state": "queued", "conversation": "c2"})
        store.emit_global({"type": "turn.state", "state": "streaming", "conversation": "c1"})
        store.emit_global({"type": "turn.state", "state": "queued", "conversation": "c1"})
        store.emit_global({"type": "kb.changed"})
        await until(lambda: seen.count("kb.changed") == 2)
        await until(lambda: seen.count("waits.changed") == 2)
        tg.cancel_scope.cancel()
