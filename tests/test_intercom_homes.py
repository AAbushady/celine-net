"""What a mission writes the user waits on the mission (Waiting for you lists the mission
once), and the app's notices into a mission are its transcript, no thread."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from celine import boot, waits
from celine.conversations import store as conversations
from celine.intercom import Intercom, deliver
from celine.intercom import store as rows
from celine.intercom.skill import IntercomSkill
from celine.missions import store as missions

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _intercom_clock(clock, monkeypatch):
    """Delivery reads the test's clock: nothing comes due by the wall clock."""
    for mod in (deliver, rows):
        monkeypatch.setattr(mod, "now", clock.iso)


def _skill(store):
    cfg = boot.load()
    skill = IntercomSkill(cfg)
    skill.app = SimpleNamespace(intercom=Intercom(store))
    return cfg, skill


async def _notices(store, kind: str) -> list[dict]:
    return [d for d in await store.read(lambda c: [json.loads(r[0]) for r in c.execute(
        "SELECT data FROM global_events WHERE type='notice' ORDER BY id")])
        if d["kind"] == kind]


async def test_a_missions_message_waits_on_the_mission_once(store):
    intercom = Intercom(store)
    night = await conversations.create(store, "Night", kind="mission")
    await missions.create(store, night, {"name": "Night", "terminal": "n", "brief": "b"})
    chat = await conversations.create(store, "Plans")
    asked = await intercom.send(f"agent:{night}", "user", "Which hotel, the cheap one?")
    flats = await intercom.send(f"agent:{chat}", "user", "I found three flats.")
    services = SimpleNamespace(store=store, approvals=None, connectors=None)

    got = await waits.items(services)
    assert [(i["kind"], i["line"]) for i in got] == [("mission", "Which hotel, the cheap one?")]
    assert (got[0]["title"], got[0]["link"]) == ("Night", f"/mission/{night}")
    await store.write(lambda c: missions.set_status_sync(c, store, night, "needs_you", "Pick one."))
    assert [i["kind"] for i in await waits.items(services)] == ["mission"], "still once"
    [m] = await store.read(lambda c: rows.listed(c, from_addr=f"agent:{night}", waiting=True))
    assert m["id"] == asked["intercom_id"], "the mission's page reads what waits"
    assert [t["thread"] for t in await store.read(lambda c: rows.threads(
        c, "user", unread=True, received=True))] == [flats["intercom_id"]], \
        "the Intercom's Waiting for you leaves it to the mission"

    # the page's reply answers it: its thread stops waiting
    await intercom.send("user", "", "the cheap one", reply_to=asked["intercom_id"])
    assert await store.read(lambda c: rows.listed(c, from_addr=f"agent:{night}", waiting=True)) == []


async def test_the_apps_notices_into_a_mission_are_no_threads(store):
    intercom = Intercom(store)
    night = await conversations.create(store, "Night", kind="mission")
    await missions.create(store, night, {"name": "Night", "terminal": "n", "brief": "b"})
    chat = await conversations.create(store, "Plans")
    await intercom.send("system:app", f"agent:{night}", "Do the next step.", kind="notice",
                        data={"mission": night, "keep_going": True}, wake=False)
    kept = await intercom.send("system:app", f"agent:{chat}", "The app is restarting now.",
                               kind="notice", wake=False)
    found = await store.read(lambda c: rows.threads(c, None))
    assert [t["thread"] for t in found] == [kept["intercom_id"]]


async def test_the_users_stop_ends_what_a_mission_asks_and_tells_the_chat_that_started_it(
        store):
    from celine.missions.skill import record
    intercom = Intercom(store)
    chat = await conversations.create(store, "Plans")
    night = await conversations.create(store, "Night", kind="mission")
    await missions.create(store, night, {"name": "Night", "terminal": "n", "brief": "b",
                                         "started_by": chat})
    await intercom.send(f"agent:{night}", "user", "Which hotel, the cheap one?")
    services = SimpleNamespace(store=store, approvals=None, connectors=None)
    assert [i["kind"] for i in await waits.items(services)] == ["mission"]

    await record(SimpleNamespace(intercom=intercom, cfg=boot.load()), store, night,
                 "stopped", None)
    assert await waits.items(services) == [], "a stopped mission waits for nothing"
    [told] = await store.read(lambda c: c.execute(
        "SELECT text FROM intercom WHERE from_addr=? AND kind='report'",
        (f"agent:{night}",)).fetchall())
    assert told[0].startswith("Mission Night stopped by ")
