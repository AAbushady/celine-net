"""Threads (store.follows): a message she sends from the turn that took one in answers in
its thread by itself, the thread holds everything oldest first, every message and thread
says where it came from (store.source_of). The user reads threads; they talk in chats."""
from __future__ import annotations

import pytest

from celine.conversations import store as conversations
from celine.intercom import Intercom, store as rows

from jobs_fixtures import cfg  # noqa: F401  (the fixture)

pytestmark = pytest.mark.anyio


async def _asked(store, ic, cid):
    """Another conversation of hers wrote to `cid` in a thread: one queued turn there."""
    other = await conversations.create(store, title="scout", kind="mission")
    first = await ic.send(f"agent:{other}", f"agent:{cid}", "the download stopped",
                          subject="videos")
    assert first["turn_id"], "it wakes her"
    return other, first


async def test_a_message_she_sends_from_that_turn_answers_in_the_thread(store, cfg):
    """The agent's instructions name the thread; no reply_to needed; one it meant as new starts
    its own; another turn under the same subject starts its own too (no endless thread
    on a word like "update")."""
    from celine.intercom.skill import IntercomSkill
    from celine.skills import ToolContext
    ic = Intercom(store)
    cid = await conversations.create(store, title="downloads", kind="mission")
    other, first = await _asked(store, ic, cid)
    tid, turn = first["intercom_id"], first["turn_id"]
    ctx = ToolContext(cid, turn, "x", store, cfg, None)  # type: ignore[arg-type]
    said = await IntercomSkill(cfg).tail_part(ctx)
    assert f'You are in thread #{tid} "videos": this turn answers #{tid}' in said
    assert f'intercom__send(to="agent:{other}") answers there by itself' in said
    line = {"thread": 9, "subject": None, "id": 9, "sub_agent": False}
    routine = IntercomSkill(cfg)._here_line({**line, "from": "system:schedules"})
    assert routine.endswith("Your reply in this conversation is the answer.")
    assert "intercom__send" not in routine, "nothing reads a message sent to a Routine"
    reminder = IntercomSkill(cfg)._here_line({**line, "from": "user"})
    assert "intercom__send" not in reminder, "they read the Intercom, they talk in chats"
    to = f"agent:{other}"
    sent = await ic.send(f"agent:{cid}", to, "relaunched it", turn=turn)
    new = await ic.send(f"agent:{cid}", to, "something else", turn=None)
    later = await ic.send(f"agent:{cid}", to, "all done", subject="videos", turn="t2")
    threads = await store.read(lambda c: {i: c.execute(
        "SELECT thread FROM intercom WHERE id=?", (i,)).fetchone()[0]
        for i in (sent["intercom_id"], new["intercom_id"], later["intercom_id"])})
    assert threads == {sent["intercom_id"]: tid, new["intercom_id"]: new["intercom_id"],
                       later["intercom_id"]: later["intercom_id"]}


async def test_the_thread_view_holds_the_whole_history_oldest_first(store):
    import httpx
    from fastapi import FastAPI

    from celine.core.services import Services
    from celine.intercom import api as intercom_api
    ic = Intercom(store)
    cid = await conversations.create(store, title="downloads", kind="mission")
    first = (await ic.send(f"agent:{cid}", "user", "the download stopped"))["intercom_id"]
    await ic.send(f"agent:{cid}", "user", "the disk was full", reply_to=first)
    app = FastAPI()
    app.state.services = Services()
    app.state.services.store = store
    app.include_router(intercom_api.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as http:
        got = (await http.get(f"/api/intercom?thread={first}")).json()
    assert [m["text"] for m in got["messages"]] == ["the download stopped", "the disk was full"]
    assert await store.read(lambda c: len(rows.thread(c, first))) == 2
    mission = {"kind": "mission", "title": "downloads", "link": f"/mission/{cid}"}
    assert got["source"] == mission
    assert [m["source"] for m in got["messages"]] == [mission, mission]


@pytest.mark.anyio
async def test_bastets_threads_wait_on_their_own_tab_not_waiting_for_you(store):
    from celine.intercom.store import insert
    cid = await conversations.create(store, title="x")

    def fn(c):
        insert(c, rows.GUARD, "user", "notice", "on patrol", store=store)
        insert(c, f"agent:{cid}", "user", "message", "hi", store=store)
        waiting = rows.threads(c, "user", unread=True, reader="user", received=True)
        guard = rows.threads(c, None, reader="user", with_=rows.GUARD)
        return [t["name"] for t in waiting], [t["name"] for t in guard], rows.waiting(c, "user")
    waiting, guard, n = await store.write(fn)
    assert waiting == ["Celine"] and guard == ["Bastet"] and n == 1
