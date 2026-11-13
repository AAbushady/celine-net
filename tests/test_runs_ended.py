"""A run's conversation after the run (schedules/runs.py): a stopped, skipped or failed run
takes what it scheduled, a done one keeps it; work a message wakes there runs under the
run's own limit, its sub-agents inside it, and is stopped at that limit."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from celine.conversations import store as conversations
from celine.core.ids import iso
from celine.schedules.runs import WOKEN_OVER, Scheduler
from chat_fixtures import user_says
from runs_fixtures import add_run
from test_schedules import Clock, _app, _s, cfg  # noqa: F401 - cfg is a fixture

pytestmark = pytest.mark.anyio
T0 = datetime(2026, 11, 13, 9, 5, tzinfo=timezone.utc)


async def _setup(store, cfg, state: str, **schedule):
    cfg.schedules = {"reflection": _s("reflection", **schedule)}
    app = _app(store, cfg)
    aborted: list[str] = []

    async def abort(cid: str) -> None:
        aborted.append(cid)
    app.dispatcher = SimpleNamespace(abort_conversation=abort, backend_factory=None)
    clock = Clock(T0)
    sched = Scheduler(app, clock)
    run = await conversations.create(store, kind="background")
    sub = await conversations.create(store, agent_of=run, agent_name="audit", kind="agent")
    await store.write(lambda c: add_run(c, "r1", "reflection", state=state, conversation_id=run))
    return app, sched, clock, run, sub, aborted


async def _scheduled(app, cid: str, to: str) -> int:
    later = iso(datetime.now(timezone.utc) + timedelta(days=1))
    return (await app.intercom.send(f"agent:{cid}", to, "measure it tomorrow",
                                    due_at=later))["intercom_id"]


async def _cancelled(store, ids) -> list[bool]:
    return await store.read(lambda c: [c.execute(
        "SELECT cancelled_at IS NOT NULL FROM intercom WHERE id = ?", (i,)).fetchone()[0] == 1
        for i in ids])


async def test_a_stopped_run_takes_what_it_and_its_sub_agents_scheduled(store, cfg):
    app, sched, _, run, sub, aborted = await _setup(store, cfg, "running")
    ids = [await _scheduled(app, run, "me"), await _scheduled(app, sub, "parent")]
    await sched.end("r1", "stopped", "Stopped by Alex")
    assert aborted == [run]
    assert await _cancelled(store, ids) == [True, True], \
        "a reminder a stopped Reflection set woke it the next morning for four hours"


async def test_a_done_run_keeps_what_it_scheduled(store, cfg):
    app, sched, _, run, _, _ = await _setup(store, cfg, "done")
    mine = await _scheduled(app, run, "me")
    assert await sched.end("r1", "stopped", "Stopped by Alex") is None, "it ended already"
    assert await _cancelled(store, [mine]) == [False], "a plan for tomorrow still reaches it"


async def test_work_woken_in_an_ended_run_runs_under_its_limit_and_stops_there(store, cfg):
    app, sched, clock, run, sub, aborted = await _setup(store, cfg, "stopped", wall_minutes=30)
    await user_says(store, run, "a reminder: measure it")          # a live turn in it
    b = await store.read(lambda c: sched._binding(c, run))
    assert b["clock"].left() == timedelta(minutes=30), "the run's own limit, from the wake"
    assert (await store.read(lambda c: sched._binding(c, sub)))["clock"] is b["clock"], \
        "its sub-agents work inside the same budget"
    later = await _scheduled(app, sub, "me")
    clock.at += timedelta(minutes=29)
    await sched._watch_woken()
    assert aborted == []
    clock.at += timedelta(minutes=2)
    await sched._watch_woken()
    assert aborted == [run], "stopped at the limit as a run is at its ceiling"
    assert await _cancelled(store, [later]) == [True]
    said = await store.read(lambda c: c.execute(
        "SELECT text, wake FROM intercom WHERE from_addr = 'system:app' AND to_addr = ?",
        (f"agent:{run}",)).fetchone())
    assert tuple(said) == (WOKEN_OVER.format(n=30, s="s"), 0), "the app's line, waking nothing"
    fresh = await store.read(lambda c: sched._binding(c, run))
    assert fresh["clock"] is not b["clock"] and fresh["clock"].left() == timedelta(minutes=30), \
        "a later wake starts a new clock"


async def test_a_woken_clock_goes_once_nothing_runs_and_a_waiting_run_gets_none(store, cfg):
    app, sched, _, run, _, aborted = await _setup(store, cfg, "done", wall_minutes=30)
    first = (await store.read(lambda c: sched._binding(c, run)))["clock"]
    await sched._watch_woken()                                      # nothing live in it
    assert sched._woken == {} and aborted == []
    await store.write(lambda c: c.execute("UPDATE runs SET state = 'waiting' WHERE id = 'r1'"))
    assert (await store.read(lambda c: sched._binding(c, run)))["clock"] is None, \
        "a run that waits for its model goes on in its own run"
    assert first is not None
