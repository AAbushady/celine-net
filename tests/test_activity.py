"""Whether a conversation is at work (celine/activity.py): Running for lists, Busy for
decisions, and what the hold does to each."""
from __future__ import annotations

import pytest

from celine import activity
from celine.conversations import store as conversations

pytestmark = pytest.mark.anyio
AT = "2026-10-10T10:00:00.000+00:00"


def _row(c, table: str, **columns) -> None:
    c.execute(f"INSERT INTO {table}({', '.join(columns)}) VALUES({', '.join('?' * len(columns))})",
              tuple(columns.values()))


async def _both(store, cid: str, models=None) -> tuple[bool, bool]:
    return await store.read(lambda c: (activity.running(c, cid, models), activity.busy(c, cid)))


async def test_a_job_that_still_runs_is_busy_and_not_running(store):
    cid = await conversations.create(store, "Backup")
    assert await _both(store, cid) == (False, False)
    await store.write(lambda c: _row(c, "jobs", id="j1", conversation_id=cid,
                                     tool_name="shell__run", state="running", created_at=AT))
    assert await _both(store, cid) == (False, True), "nothing executes, its result still comes"
    listed = {r["id"]: r for r in await conversations.list(store)}
    assert listed[cid]["group"] != "Running" and listed[cid]["state"] is None


async def test_a_turn_runs_and_a_queued_one_the_hold_keeps_aside_does_not(store):
    cid = await conversations.create(store, "Review")
    await store.write(lambda c: _row(c, "turns", id="t1", conversation_id=cid, state="queued",
                                     created_at=AT))
    assert await _both(store, cid) == (True, True)
    assert {r["id"]: r["group"] for r in await conversations.list(store)}[cid] == "Running"
    await store.write(lambda c: _row(c, "kv", k="frozen", v="{}"))
    assert await _both(store, cid) == (False, True), "frozen: it has not started, and it will"
    row = {r["id"]: r for r in await conversations.list(store)}[cid]
    assert row["group"] != "Running"
    assert row["state"] == {"tone": "warn", "words": "Frozen"}, "its row says why it waits"
    background = await conversations.create(store, "Mail", kind="background",
                                            trigger_key="trigger:new_mail")
    await store.write(lambda c: _row(c, "turns", id="t2", conversation_id=background,
                                     state="queued", created_at=AT))
    [task] = await conversations.background(store)
    assert (task["working"], task["state"]) == (False, {"tone": "warn", "words": "Frozen"}), \
        "the background list says the same as the chat list"


async def test_a_sub_agent_at_work_makes_its_parent_busy_and_the_family_running(store):
    parent = await conversations.create(store, "Plan")
    sub = await conversations.create(store, agent_of=parent, agent_name="digger", kind="agent")
    await store.write(lambda c: _row(c, "turns", id="t1", conversation_id=sub, state="streaming",
                                     created_at=AT))
    assert await _both(store, parent) == (False, True), "its own turn does not run, its sub-agent's does"
    assert {r["id"]: r["group"] for r in await conversations.list(store)}[parent] == "Running"
