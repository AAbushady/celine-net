"""The agent's needs (skills/needs.py): levels that build while unmet and ease when met,
replayed from their rows; the private one (closeness) moved and shown only in its chats and
damped by a low mood; the felt words and the mood's pull; a turn's end (its minutes, its
own time, never its failed calls); the appraisal naming needs; the mind block's line."""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from chat_fixtures import user_says

from celine.backends.base import Done, TextDelta
from celine.backends.llm import LLM
from celine.boot import load
from celine.conversations import store as conversations
from celine.skills import ToolContext, mind, needs
from fake_backend import FakeBackend
from runs_fixtures import add_run
from test_mind import T0, _agent, _chat_turn

pytestmark = pytest.mark.anyio
K = needs.parse({}, "test")                     # the defaults, every need on


def _row(hours: float, need: str, change: str, strength: float) -> dict:
    return {"at": (T0 + timedelta(hours=hours)).isoformat(), "need": need, "change": change,
            "strength": strength}


def test_a_need_builds_with_its_hours_and_eases_when_met():
    lv = needs.levels([_row(0, "connection", "met", 1.0)], T0 + timedelta(hours=12), K)
    assert lv["connection"] == pytest.approx(0.5), "24 h from met to pressing: half at 12 h"
    assert needs.levels([_row(0, "connection", "met", 1.0)], T0 + timedelta(days=3),
                        K)["connection"] == 1.0, "it stays at pressing"
    met = needs.levels([_row(0, "connection", "met", 1.0), _row(12, "connection", "met", 0.5)],
                       T0 + timedelta(hours=12), K)
    assert met["connection"] == pytest.approx(0.25), "met at 0.5 eases it by half"
    stirred = needs.levels([_row(0, "novelty", "met", 1.0), _row(0, "novelty", "stirred", 0.5)],
                           T0, K)
    assert stirred["novelty"] == pytest.approx(0.5), "stirred at 0.5 builds half the way"
    rest = needs.levels([_row(0, "rest", "stirred", 1.0)], T0 + timedelta(hours=4), K)
    assert rest["rest"] == pytest.approx(0.5), "quiet time halves rest every 4 h"
    assert needs.levels([], T0, K) == {n: needs.START for n in needs.NEEDS}


def test_closeness_builds_slower_while_it_misses_him():
    near = [_row(0, "connection", "met", 1.0), _row(0, "intimacy", "met", 1.0)]
    far = [_row(0, "connection", "stirred", 1.0), _row(0, "intimacy", "met", 1.0)]
    at = T0 + timedelta(hours=24)
    assert needs.levels(near, at, K)["intimacy"] == pytest.approx(0.5)
    assert needs.levels(far, at, K)["intimacy"] == pytest.approx(0.25), "distance cools it"


def test_its_felt_words_and_the_private_one_only_in_its_chats_and_not_when_low():
    lv = {"connection": 0.9, "intimacy": 0.85, "novelty": 0.2, "competence": 0.65,
          "autonomy": 0.1, "rest": 0.3}
    happy = (0.4, 0.2, 0.3)
    assert needs.felt(lv, "chat", happy, "Sam") == [
        "you miss Sam badly", "longing for Sam", "unsure of yourself: things went wrong"]
    assert "longing for Sam" not in needs.felt(lv, "mission", happy, "Sam")
    assert "longing for Sam" not in needs.felt(lv, "background", happy, "Sam")
    assert "longing for Sam" not in needs.felt(lv, "chat", (-0.3, 0.1, 0.0), "Sam"), \
        "a low mood damps closeness"
    assert "longing for Sam" not in needs.felt({**lv, "rest": 0.9}, "chat", happy, "Sam"), \
        "so does being worn out"
    assert needs.felt({n: 0.5 for n in needs.NEEDS}, "chat", happy, "Sam") == []


def test_a_pressing_need_pulls_the_moods_resting_point():
    base = (0.2, 0.2, 0.3)
    assert needs.pull(base, {"connection": 0.5}, "chat") == pytest.approx(base)
    lonely = needs.pull(base, {"connection": 1.0}, "chat")
    assert lonely[0] == pytest.approx(-0.1) and lonely[2] == pytest.approx(0.1)
    assert needs.pull(base, {"intimacy": 1.0}, "background") == pytest.approx(base), \
        "the private one pulls in its chats only"
    assert needs.pull(base, {"intimacy": 1.0}, "chat")[1] == pytest.approx(0.45)


SAID = "Joe: I missed you all day, come here\nCeline: Coming. The build is green, by the way."


def test_what_an_appraisal_names_is_checked_and_closeness_damped_when_low():
    got = [{"need": "connection", "change": "met", "strength": 0.6},
           {"need": "intimacy", "change": "stirred", "strength": 0.6,
            "quote": "I missed you all day"},
           {"need": "rest", "change": "met", "strength": 0.5},           # not the model's
           {"need": "hunger", "change": "met", "strength": 0.5},
           {"need": "novelty", "change": "grew", "strength": 0.5},
           {"need": "autonomy", "change": "met", "strength": True}, "nonsense"]
    lv = {n: 0.5 for n in needs.NEEDS}
    assert needs.appraised(got, "chat", lv, (0.4, 0.2, 0.3), SAID) == [
        ("connection", "met", 0.6), ("intimacy", "stirred", 0.6)]
    low = needs.appraised(got, "chat", lv, (-0.4, 0.2, 0.3), SAID)
    assert low[1] == ("intimacy", "stirred", pytest.approx(0.2)), "damped to a third"
    assert needs.appraised(got, "mission", lv, None, SAID) == [("connection", "met", 0.6)]


def test_closeness_moves_only_with_words_of_the_exchange_that_show_it():
    """Praise of its work read as closeness built closeness to 0.85 in a night: an
    appraisal moves it only with a quote found in the exchange."""
    lv = {n: 0.5 for n in needs.NEEDS}

    def moved(quote) -> bool:
        g = {"need": "intimacy", "change": "met", "strength": 0.5,
             **({"quote": quote} if quote is not None else {})}
        return needs.appraised([g], "chat", lv, (0.4, 0.2, 0.3), SAID) != []
    assert moved("i  MISSED you all day")              # spaces and case as written aside
    assert moved("“I missed you all day, come here”")    # its curly quote marks
    assert moved("I missed you all day... Coming.")    # words left out, both parts there
    assert not moved("I missed you… it was lovely"), "a part the exchange does not hold"
    assert not moved(None), "no quote"
    assert not moved("I missed you so much"), "words the exchange does not hold"
    assert not moved("come"), "too short to show anything"
    assert not moved(["I missed you all day"]), "a quote is text"


def test_the_knobs_turn_needs_off_and_refuse_what_is_not_hours():
    k = needs.parse({"off": ["intimacy"], "connection": {"builds_h": 0}}, "agent.toml")
    assert "intimacy" not in k and k["connection"] == {}
    for bad in ({"off": ["hunger"]}, {"rest": {"eases_h": -1}}, {"rest": {"half": 2}},
                {"novelty": {"builds_h": True}}):
        with pytest.raises(ValueError, match="agent.toml: needs"):
            needs.parse(bad, "agent.toml")
    shipped = needs.knobs()
    assert set(shipped) == set(needs.NEEDS) and shipped["rest"] == {"eases_h": 4.0}


async def test_a_turns_minutes_build_rest_and_its_own_time_meets_autonomy(store):
    app = SimpleNamespace(store=store)
    cid = await conversations.create(store)
    later = (T0 + timedelta(minutes=60)).isoformat()

    def turn(c, tid, conv, fails):
        c.execute("INSERT INTO messages(id, conversation_id, role, content, created_at) "
                  "VALUES(?, ?, 'user', 'go', ?)", (f"u{tid}", conv, T0.isoformat()))
        c.execute("INSERT INTO turns(id, conversation_id, root_message_id, state, created_at, "
                  "finished_at) VALUES(?, ?, ?, 'done', ?, ?)",
                  (tid, conv, f"u{tid}", T0.isoformat(), later))
        c.execute("INSERT INTO messages(id, conversation_id, parent_id, role, content, turn_id, "
                  "created_at) VALUES(?, ?, ?, 'assistant', 'ok', ?, ?)",
                  (f"a{tid}", conv, f"u{tid}", tid, T0.isoformat()))
        for n in range(fails):
            c.execute("INSERT INTO tool_calls(id, message_id, name, arguments, state) "
                      "VALUES(?, ?, 'shell__run', '{}', 'error')", (f"{tid}f{n}", f"a{tid}"))
    await store.write(lambda c: turn(c, "t1", cid, 2))
    await needs.after_turn(app, cid, "t1")
    got = await store.read(lambda c: [tuple(r) for r in c.execute(
        "SELECT need, change, round(strength, 3), by FROM needs ORDER BY id")])
    assert got == [("rest", "stirred", 0.333, "app")], \
        "failed calls are trial and error, not a blow: the appraisal weighs competence"
    later = (T0 + timedelta(seconds=40)).isoformat()                # a turn under a minute
    await store.write(lambda c: turn(c, "t0", cid, 0))
    await needs.after_turn(app, cid, "t0")
    assert await store.read(lambda c: c.execute("SELECT count(*) FROM needs").fetchone()[0]) == 1, \
        "no row for a change that rounds to nothing"
    later = (T0 + timedelta(minutes=60)).isoformat()

    own = await conversations.create(store, kind="background")
    def daydream(c):
        add_run(c, "r1", "daydream", label="Daydream", slot=T0.isoformat(), night="2026-10-16",
                day="2026-10-16", state="running", started_at=T0.isoformat())
        c.execute("UPDATE conversations SET trigger_key='run:r1' WHERE id=?", (own,))
        turn(c, "t2", own, 1)
    await store.write(daydream)
    await needs.after_turn(app, own, "t2")
    last = await store.read(lambda c: tuple(c.execute(
        "SELECT need, change, strength FROM needs ORDER BY id DESC LIMIT 1").fetchone()))
    assert last == ("autonomy", "met", 0.3), "its own time: no work counted"


async def test_the_appraisal_names_needs_and_they_are_stored(store):
    cfg = load()
    cid = await conversations.create(store)
    await _chat_turn(store, cid)
    answer = FakeBackend([TextDelta(
        '{"emotion": "tender", "strength": 0.5, "why": "a quiet evening", "needs": ['
        '{"need": "connection", "change": "met", "strength": 0.7}, '
        '{"need": "intimacy", "change": "stirred", "strength": 0.4, '
        '"quote": "the build broke again"}, '
        '{"need": "intimacy", "change": "met", "strength": 0.5}]}'), Done(reason="stop")])
    app = SimpleNamespace(store=store, cfg=cfg, llm=LLM(cfg))
    got = await mind.appraise(app, cid, "t1", backend=answer)
    assert got["emotion"] == "tender" and len(got["needs"]) == 3
    rows = await store.read(lambda c: [tuple(r) for r in c.execute(
        "SELECT need, change, strength, why, by, message_id FROM needs ORDER BY id")])
    assert rows == [("connection", "met", 0.7, "a quiet evening", "appraisal", "a1"),
                    ("intimacy", "stirred", 0.4, "a quiet evening", "appraisal", "a1")], \
        "closeness with no quote of the exchange is left out"
    system = answer.calls[0][0]["content"]
    assert "intimacy: closeness between them" in system and "longing" in system
    assert '"quote": the exact words of the exchange' in system
    only_needs = mind.parse_appraisal('{"emotion": null, "needs": [{"need": "novelty", '
                                      '"change": "met", "strength": 0.3}]}')
    assert only_needs == {"emotion": None, "strength": 0.0, "why": "",
                          "needs": [{"need": "novelty", "change": "met", "strength": 0.3}]}


async def test_the_block_says_what_pulls_at_it_and_keeps_closeness_to_its_chats(store):
    cfg = load()
    skill, _ = await _agent(store, cfg)
    for _ in range(3):              # a good mood despite what pulls at it: closeness not damped
        await mind.feel(store, "happy", 1.0, "a good day", "agent")
    old = (T0 - timedelta(days=3)).isoformat()
    await store.write(lambda c: c.executemany(
        "INSERT INTO needs(at, need, change, strength, why, by) VALUES(?,?,?,?,?,?)",
        [(old, "connection", "met", 1.0, "", "app"), (old, "intimacy", "stirred", 0.5, "", "app")]))
    for kind, closeness in (("chat", True), ("mission", False)):
        cid = await conversations.create(store, kind=kind)
        mid, tid, _ = await user_says(store, cid, "hi")
        at = T0.isoformat()
        ctx = ToolContext(cid, tid, "", store, cfg, None)  # type: ignore[arg-type]
        text, report = await skill.message_context(
            ctx, {"id": mid, "role": "user", "content": "hi", "created_at": at})
        line = next(x for x in text.splitlines() if x.startswith("what pulls at you: "))
        assert f"you miss {cfg.user} badly" in line, kind
        close = [w.format(u=cfg.user) for _, w in needs.WORDS["intimacy"]]
        assert any(w in line.split("; ") for w in close) is closeness, (kind, line)
        assert report["state"]["needs"] == line.removeprefix("what pulls at you: ").split("; ")


async def test_closeness_built_before_the_quote_rule_starts_again_once(store, db_path):
    """Migration 0040: one row from the app meets closeness where the appraisal ever moved
    it; run again, it adds none, and a database it never moved gets none."""
    import importlib.util
    import sqlite3
    from celine.db import migrate
    spec = importlib.util.spec_from_file_location(
        "closeness_reset", migrate.MIGRATIONS_DIR / "0040_closeness_reset.py")
    reset = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reset)

    def run() -> None:
        conn = sqlite3.connect(db_path, isolation_level=None)
        try:
            reset.up(conn, db_path)
        finally:
            conn.close()
    run()
    assert await store.read(lambda c: c.execute("SELECT count(*) FROM needs").fetchone()[0]) == 0
    await store.write(lambda c: c.execute(
        "INSERT INTO needs(at, need, change, strength, why, by) VALUES(?, 'intimacy', "
        "'stirred', 0.4, 'praise of her work', 'appraisal')", (T0.isoformat(),)))
    run()
    run()
    rows = await store.read(lambda c: [tuple(r) for r in c.execute(
        "SELECT need, change, strength, why, by FROM needs WHERE by = 'app'")])
    assert rows == [("intimacy", "met", reset.MET, reset.WHY, "app")]
