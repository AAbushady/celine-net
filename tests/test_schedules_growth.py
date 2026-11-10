"""Schedules that make room for the user, append instead of overwrite, keep backups,
and the growth jobs that ship with a fresh install (schedules/)."""
from __future__ import annotations

import lzma
import sqlite3
import tarfile
import tomllib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from chat_fixtures import user_says

from celine.backends.queue import Priority
from celine.config import ROOT
from celine.boot import load
from celine.conversations import store as conversations
from celine.core.bus import EventBus
from celine.core.services import Services
from celine.intercom import Intercom
from celine.schedules import Schedule, parse, read_reviewer, runs as runs_mod
from celine.schedules.backups import nightly
from celine.schedules.runs import IN_USE, PASSED, Scheduler, binding, waited_on
from celine.schedules.variables import tool_failures
from celine.skills import Registry
from celine.skills.notebook import Notebook
from celine.turns.dispatcher import Dispatcher
from runs_fixtures import add_run

pytestmark = pytest.mark.anyio
UTC = timezone.utc
CONFIG = ROOT / "config" / "schedules"


@pytest.fixture
def cfg():
    c = load()
    c.timezone = "UTC"
    c.user = "Joe"
    c.settings.features["done"] = True       # the onboarding is done: routines run
    return c


def _app(store, cfg, llm=None):
    reg = Registry()
    reg.add(Notebook(cfg))
    app = Services(cfg=cfg, store=store, registry=reg, llm=llm)
    app.dispatcher = SimpleNamespace(backend_factory=lambda: None)
    app.intercom = Intercom(store)
    for s in reg.skills.values():
        s.app = app
    return app


def test_every_shipped_schedule_loads_through_the_parser():
    names = sorted(p.stem for p in CONFIG.glob("*.toml"))
    assert {"daydream", "consolidate", "radar", "watch", "practice", "tidy",
            "growth"} <= set(names)
    for name in names:
        raw = tomllib.loads((CONFIG / f"{name}.toml").read_text())
        s = parse(name, raw, read_reviewer(CONFIG, str(raw.get("reviewer") or "")))
        assert "—" not in s.prompt and " · " not in s.prompt
    from celine.errors import CelineError
    with pytest.raises(CelineError):
        parse("x", {"label": "X", "cron": "0 2 * * *", "prompt": "p", "wall_minutes": 0}, None)


async def test_the_reviewer_binds_behind_the_user_with_the_schedules_label(store, cfg):
    bound = {}
    llm = SimpleNamespace(bind=lambda backend, **kw: bound.update(kw) or "b")
    sched = Scheduler(_app(store, cfg, llm))
    s = Schedule("reflection", "Reflection", "0 5 * * *", "p")
    assert sched._reviewer_backend("cid", s) == "b"
    assert (bound["priority"], bound["label"], bound["fallback"]) == \
        (Priority.BACKGROUND, "Reflection", False), "on the role's own model only"

    cfg.schedules = {"reflection": s}
    run_cid = await conversations.create(store, "run")
    sub = await conversations.create(store, "scout", agent_of=run_cid, agent_name="scout")
    deep = await conversations.create(store, "digger", agent_of=sub, agent_name="digger")
    await store.write(lambda c: add_run(c, "r", "reflection", night="2026-10-16", state="running",
                                        conversation_id=run_cid))
    for cid in (run_cid, sub, deep):            # every depth binds as the run
        assert await store.read(lambda c: binding(c, cid, cfg)) == {
            "priority": Priority.BACKGROUND, "label": "Reflection", "run": "r"}
    chat = await conversations.create(store, "talk")
    assert await store.read(lambda c: binding(c, chat, cfg)) is None

    d = Dispatcher(store, cfg, EventBus(), lambda: None, Registry())
    d.llm = SimpleNamespace(bind=lambda backend, **kw: kw)
    run = await store.read(lambda c: binding(c, deep, cfg))
    kw = d._backend_for({"id": "t", "conversation_id": deep, "origin": "intercom"},
                        None, run=run)
    assert (kw["priority"], kw["label"], kw["fallback"]) == \
        (Priority.BACKGROUND, "Reflection", False)
    kw = d._backend_for({"id": "t2", "conversation_id": chat, "origin": "intercom"}, None)
    assert kw["priority"] == Priority.AGENT


async def test_the_start_gate_sees_a_live_mission(store):
    scene = await conversations.create(store, "mission", kind="mission")
    run = await conversations.create(store, "run", trigger_key="run:r")
    await store.write(lambda c: c.execute("UPDATE conversations SET kind='background' "
                                          "WHERE id=?", (run,)))
    await user_says(store, run, "go")
    assert not await store.read(waited_on), "a run's own turn is nobody's wait"
    await user_says(store, scene, "hello")
    assert await store.read(waited_on)


async def test_a_mission_that_goes_on_by_itself_holds_no_routine_unless_asked(store, cfg):
    from celine.intercom import send
    scene = await conversations.create(store, "mission", kind="mission")
    await send(store, "system:app", f"agent:{scene}", "Do the next step.", kind="notice")
    assert not await store.read(waited_on), "its own turn: nobody waits on it"
    assert await store.read(lambda c: waited_on(c, missions=True))

    cfg.schedules = {"world": Schedule("world", "World", "0 2 1 1 *", "p")}
    sched = Scheduler(_app(store, cfg))
    run = await sched.run_now("world")
    cfg.settings.general["routines_wait_for_missions"] = True
    await sched.tick()
    assert await store.read(lambda c: c.execute(
        "SELECT reason FROM runs WHERE id=?", (run["id"],)).fetchone()[0]) == \
        "Waiting until your chat is done", "Advanced: routines wait for missions"


async def test_a_routine_she_switches_off_is_told_to_the_user_and_daytime_can_be_set(
        store, cfg, monkeypatch):
    from celine.schedules.skill import Routines
    from celine.skills import ToolContext
    cfg.schedules = {"world": Schedule("world", "World", "0 2 * * *", "p")}
    skill = Routines(cfg)
    skill.app = _app(store, cfg)

    async def save(app, name, body, by):
        s = Schedule(name, body["label"], body["cron"], body["prompt"], on=body["on"],
                     daytime=body["daytime"])
        cfg.schedules[name] = s
        return s, True
    monkeypatch.setattr("celine.schedules.edit.save", save)
    ctx = ToolContext(conversation_id="c", turn_id="t", tool_call_id="x", store=store,
                      config=cfg, emit=None)  # type: ignore[arg-type]

    async def told():
        return await store.read(lambda c: [tuple(r) for r in c.execute(
            "SELECT from_addr, kind, text FROM intercom WHERE to_addr='user'")])
    out = await skill.write(ctx, name="world", daytime=True)
    assert not out.is_error and cfg.schedules["world"].daytime, out.content
    assert await told() == [], "a change that leaves it on says nothing"
    out = await skill.write(ctx, name="world", on=False)
    assert not out.is_error and out.content.endswith(", off."), out.content
    assert await told() == [("system:app", "notice",
                             f"Routine World was switched off by {cfg.substitute('{{name}}')}.")]


async def test_a_chat_held_on_an_approval_holds_no_routine(store, cfg):
    """One unanswered approval held a night's routines until morning. Its turn asks the
    model nothing until the answer, and a chat that is working still holds them."""
    chat = await conversations.create(store, "chat")
    await user_says(store, chat, "hello")
    assert await store.read(waited_on)
    assert not await store.read(lambda c: waited_on(c, parked=frozenset({chat})))
    assert await store.read(lambda c: waited_on(c, parked=frozenset({"another"})))

    cfg.schedules = {"world": Schedule("world", "World", "0 2 1 1 *", "p")}
    app = _app(store, cfg)
    sched = Scheduler(app)
    run = await sched.run_now("world")
    started = []
    sched._start = lambda r: started.append(r["id"])

    app.approvals = SimpleNamespace(waiting={})
    await sched.tick()
    assert not started and await store.read(lambda c: c.execute(
        "SELECT reason FROM runs WHERE id=?", (run["id"],)).fetchone()[0]) == \
        "Waiting until your chat is done"
    app.approvals = SimpleNamespace(waiting={"a": SimpleNamespace(conversation=chat)})
    await sched.tick()
    assert started == [run["id"]]


async def test_an_idle_only_run_is_skipped_while_the_app_is_in_use(store, cfg):
    clock = SimpleNamespace(at=datetime.now(UTC))
    # its own time is next month, so only the runs started here wait
    cfg.schedules = {"daydream": Schedule(
        "daydream", "Daydream", f"0 10 1 {clock.at.month % 12 + 1} *", "p", idle_only=True)}
    sched = Scheduler(_app(store, cfg), lambda: clock.at)
    chat = await conversations.create(store, "talk")
    await user_says(store, chat, "hi")
    await store.write(lambda c: c.execute("UPDATE turns SET state='done'"))
    run = await sched.run_now("daydream", by="agent")      # the user's own is never skipped
    await sched.tick()
    got = await store.read(lambda c: dict(c.execute(
        "SELECT state, reason FROM runs WHERE id=?", (run["id"],)).fetchone()))
    assert got == {"state": "skipped", "reason": IN_USE}, "a message came a moment ago"
    clock.at += timedelta(seconds=1)
    later = await sched.run_now("daydream", by="agent")
    clock.at += timedelta(hours=1)
    await sched.tick()
    assert await store.read(lambda c: c.execute(
        "SELECT reason FROM runs WHERE id=?", (later["id"],)).fetchone()[0]) == PASSED, \
        "never caught up"


async def test_a_note_that_is_there_gets_the_answer_under_a_heading(store, cfg):
    sched = Scheduler(_app(store, cfg))
    s = Schedule("review", "Self-review", "0 4 * * *", "p", note="diary/x.md")
    cid = await conversations.create(store, "run")
    note = cfg.agent.notebook / "diary" / "x.md"
    assert await sched._write_note(cid, "diary/x.md", "First.", s, "2026-10-16") is None
    assert note.read_text().strip().endswith("\nFirst.")
    await sched._write_note(cid, "diary/x.md", "Second.", s, "2026-10-17")
    assert note.read_text().strip().endswith("First.\n\n## 2026-10-17 Self-review\n\nSecond.")


async def test_a_note_the_agent_touched_since_the_run_began_is_its_own_whole(store, cfg):
    """A run that goes on after a restart: what it wrote before it is the result."""
    import os
    sched = Scheduler(_app(store, cfg))
    note = cfg.agent.notebook / "diary" / "w.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# World\n\nThe news.\n")
    at = datetime(2026, 10, 17, 2, tzinfo=UTC)
    os.utime(note, (at.timestamp(), at.timestamp()))
    assert not sched._note_touched("diary/w.md", "2026-10-17T02:30:00+00:00")
    assert sched._note_touched("diary/w.md", "2026-10-17T01:30:00+00:00")
    assert sched._take("diary/w.md", None, "Done.", True) == ("# World\n\nThe news.", True)
    assert sched._take("diary/w.md", None, "Done.", False) == ("Done.", False)


def test_the_nightly_backups_are_made_once_a_night_and_seven_kept(tmp_path):
    nb = tmp_path / "notebook"
    (nb / ".versions").mkdir(parents=True)
    (nb / "a.md").write_text("a")
    (nb / ".versions" / "a.md.1").write_text("old a")
    db = tmp_path / "celine.db"
    sqlite3.connect(db).execute("CREATE TABLE t(x)").connection.commit()
    sqlite3.connect(tmp_path / "log.db").execute("CREATE TABLE e(x)").connection.commit()
    made = nightly(tmp_path, db, nb, date(2026, 10, 16))
    assert [p.relative_to(tmp_path).as_posix() for p in made] == [
        "backups/notebook/2026-10-16.tar.xz", "backups/db/2026-10-16.db.xz",
        "backups/log/2026-10-16.db.xz"]
    with tarfile.open(made[0]) as tar:
        assert {"notebook/a.md", "notebook/.versions/a.md.1"} <= set(tar.getnames())
    restored = tmp_path / "restored.db"
    restored.write_bytes(lzma.decompress(made[1].read_bytes()))
    assert sqlite3.connect(restored).execute("SELECT name FROM sqlite_master").fetchone() \
        == ("t",)
    restored.write_bytes(lzma.decompress(made[2].read_bytes()))
    assert sqlite3.connect(restored).execute("SELECT name FROM sqlite_master").fetchone() \
        == ("e",)
    assert nightly(tmp_path, db, nb, date(2026, 10, 16)) == [], "once a night"
    assert not list(Path(tmp_path, "backups").rglob("*.part"))
    (tmp_path / "backups" / "db" / "notes.xz").write_text("not a night")
    for n in range(1, 10):
        nightly(tmp_path, db, None, date(2026, 10, 16) + timedelta(days=n))
    kept = sorted(p.name for p in (tmp_path / "backups" / "db").iterdir())
    assert len(kept) == 7 + 1 and kept[0] == "2026-10-19.db.xz", "seven nights kept"
    assert "notes.xz" in kept, "a name that is not a night stays"


async def test_tool_failures_say_each_error_and_the_conversations_it_happened_in(store):
    """The tally gave a tool's first error and every conversation where the tool failed
    for any reason, by title alone: two runs with one title read as one, and the review
    looked for an error where it had not happened."""
    first = await conversations.create(store, "Searching")
    second = await conversations.create(store, "Searching")

    def fn(c):
        for i, (cid, name, state, error, at) in enumerate((
                (first, "web__search", "error", "SearXNG is down", "2026-10-15T20:00:00.000+00:00"),
                (second, "web__search", "error", "SearXNG is down", "2026-10-15T21:00:00.000+00:00"),
                (second, "web__search", "error", "bad query", "2026-10-15T21:30:00.000+00:00"),
                (first, "shell__run", "error", "exit 1", "2026-10-16T01:00:00.000+00:00"),
                (first, "web__search", "ok", "found", "2026-10-15T22:00:00.000+00:00"),
                (first, "web__search", "error", "SearXNG is down", "2026-10-14T20:00:00.000+00:00"),
                *((first, "notebook__read", "error", f"n{k}.md does not exist",
                   "2026-10-16T02:00:00.000+00:00") for k in range(5)))):
            c.execute("INSERT INTO messages(id, conversation_id, role, content, created_at) "
                      "VALUES(?, ?, 'assistant', '', ?)", (f"m{i}", cid, at))
            c.execute("INSERT INTO messages(id, conversation_id, role, content, created_at) "
                      "VALUES(?, ?, 'tool', ?, ?)", (f"r{i}", cid, error, at))
            c.execute("INSERT INTO tool_calls(id, message_id, result_message_id, name, "
                      "arguments, state, started_at) VALUES(?, ?, ?, ?, '{}', ?, ?)",
                      (f"t{i}", f"m{i}", f"r{i}", name, state, at))
    await store.write(fn)
    got = await store.read(lambda c: tool_failures(c, date(2026, 10, 16), UTC))
    a, b = f"Searching {first[:8]}", f"Searching {second[:8]}"
    assert got == (
        "- notebook__read: 5 failed.\n"
        f"  - once: n0.md does not exist In: {a}.\n"
        f"  - once: n1.md does not exist In: {a}.\n"
        f"  - once: n2.md does not exist In: {a}.\n"
        "  - 2 more failed with 2 other errors.\n"
        "- web__search: 3 failed.\n"
        f"  - 2 times: SearXNG is down In: {a}, {b}.\n"
        f"  - once: bad query In: {b}.\n"
        "- shell__run: 1 failed.\n"
        f"  - once: exit 1 In: {a}.")
    assert await store.read(lambda c: tool_failures(c, date(2026, 10, 1), UTC)) == \
        runs_mod.NONE


async def test_voice_counts_compare_its_replies_with_the_users(store):
    from celine.schedules.variables import voice_counts
    cid = await conversations.create(store, "talk")
    now = datetime.now(UTC)

    def fn(c):
        rows = [("u1", "user", "user", "keep it short pls"),
                ("a1", "assistant", "model", "ok pal, one two three."),
                ("u2", "user", "user", "and now?"),
                ("a2", "assistant", "model", "Sure. Here is a long answer")]
        for i, (mid, role, author, text) in enumerate(rows):
            c.execute("INSERT INTO messages(id, conversation_id, role, author, content, "
                      "created_at) VALUES(?, ?, ?, ?, ?, ?)",
                      (mid, cid, role, author, text, (now - timedelta(minutes=10 - i))
                       .isoformat(timespec="milliseconds")))
    await store.write(fn)
    got = await store.read(lambda c: voice_counts(c, now, {}))
    assert "Your chat replies of the last 7 days: 2" in got
    assert "Replies that start lowercase: 50%." in got
    assert "your reply was 5 words (median of 1)" in got


async def test_the_clock_starts_with_the_run_and_its_ceiling_wakes_the_scheduler(
        store, cfg):
    clock = SimpleNamespace(at=datetime(2026, 10, 10, 2, tzinfo=UTC))
    cfg.schedules = {"world": Schedule("world", "World", "0 2 * * *", "p", wall_minutes=10)}
    sched = Scheduler(_app(store, cfg), lambda: clock.at)
    run = await sched.run_now("world")
    sched._tg = SimpleNamespace(start_soon=lambda *a: None)
    sched._start(run)
    await store.write(lambda c: c.execute("UPDATE runs SET state='running' WHERE id=?",
                                          (run["id"],)))
    assert sched._clock.left() == timedelta(minutes=10), "the clock starts with the run"
    clock.at += timedelta(minutes=9, seconds=30)
    assert sched.until_next() == 30, "the scheduler looks again at its ceiling"
    clock.at += timedelta(seconds=30)
    await sched.tick()
    assert await store.read(lambda c: c.execute(
        "SELECT state FROM runs WHERE id=?", (run["id"],)).fetchone()[0]) == "running", \
        "at its ceiling, not past it"
    clock.at += timedelta(seconds=1)
    await sched.tick()
    assert await store.read(lambda c: tuple(c.execute(
        "SELECT state, error FROM runs WHERE id=?", (run["id"],)).fetchone())) == \
        ("failed", "Stopped at its time limit of 10 minutes.")


async def test_a_routine_time_limit_of_0_lets_a_run_go_on(store, cfg):
    clock = SimpleNamespace(at=datetime(2026, 10, 10, 2, tzinfo=UTC))
    cfg.settings.general["routine_max_minutes"] = 0
    cfg.schedules = {"world": Schedule("world", "World", "0 2 * * *", "p")}
    sched = Scheduler(_app(store, cfg), lambda: clock.at)
    run = await sched.run_now("world")
    sched._tg = SimpleNamespace(start_soon=lambda *a: None)
    sched._start(run)
    await store.write(lambda c: c.execute("UPDATE runs SET state='running' WHERE id=?",
                                          (run["id"],)))
    clock.at += timedelta(days=2)
    await sched.tick()
    assert await store.read(lambda c: c.execute(
        "SELECT state FROM runs WHERE id=?", (run["id"],)).fetchone()[0]) == "running"
