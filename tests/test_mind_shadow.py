"""Shadow appraisal (skills/mind_shadow.py): the turns the appraisal after a chat turn leaves
out are read on the same role, from the agent's words and the app's own rows of its tool
calls, never its thinking or the run's prompt; kept in `shadow_feelings`, also when nothing
is named, beside what the agent named; never moving the mood or the needs; a sub-agent's
turn and a chat turn the live appraisal reads are left out."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from celine.backends.base import Done, TextDelta
from fake_backend import FakeBackend
from celine.backends.llm import LLM
from celine.boot import load
from celine.conversations import store as conversations
from celine.skills import mind_shadow
from runs_fixtures import add_run

import pytest

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)


async def _turn(store, cid, *, feel=False, failed_call=True):
    """A finished turn: the prompt, a reply with thinking, a call, and a last reply."""
    def fn(c):
        at = T0.isoformat()
        c.execute("INSERT INTO messages(id, conversation_id, role, content, created_at) "
                  "VALUES('u1', ?, 'user', 'Your mood: tender. This is your own time.', ?)",
                  (cid, at))
        c.execute("INSERT INTO turns(id, conversation_id, root_message_id, state, "
                  "created_at) VALUES('t1', ?, 'u1', 'done', ?)", (cid, at))
        c.execute("INSERT INTO messages(id, conversation_id, parent_id, role, content, "
                  "reasoning, turn_id, created_at) VALUES('a1', ?, 'u1', 'assistant', "
                  "'trying the build once more', 'SECRET DRAFT', 't1', ?)", (cid, at))
        c.execute("INSERT INTO messages(id, conversation_id, parent_id, role, content, "
                  "turn_id, created_at) VALUES('r1', ?, 'a1', 'tool', "
                  "'make: *** [all] Error 2', 't1', ?)", (cid, at))
        c.execute("INSERT INTO tool_calls(id, message_id, result_message_id, name, "
                  "arguments, state) VALUES('tc1', 'a1', 'r1', 'shell__run', ?, ?)",
                  (json.dumps({"command": "make"}), "error" if failed_call else "ok"))
        if feel:
            c.execute("INSERT INTO tool_calls(id, message_id, name, arguments, state) "
                      "VALUES('tc2', 'a1', 'mind__feel', ?, 'ok')",
                      (json.dumps({"emotion": "proud", "strength": 0.6, "why": "it built"}),))
        c.execute("INSERT INTO messages(id, conversation_id, parent_id, role, content, "
                  "turn_id, created_at) VALUES('a2', ?, 'r1', 'assistant', "
                  "'it finally built after two days', 't1', ?)",
                  (cid, (T0 + timedelta(seconds=1)).isoformat()))
    await store.write(fn)


async def _daydream(store):
    cid = await conversations.create(store, "run", trigger_key="run:r1", kind="background")
    await store.write(lambda c: add_run(c, "r1", "daydream", label="Daydream",
                                        conversation_id=cid))
    return cid


def _app(store, cfg):
    return SimpleNamespace(store=store, cfg=cfg, llm=LLM(cfg))


def _says(answer: str):
    return FakeBackend([TextDelta(answer), Done(reason="stop")])


async def test_its_own_time_is_read_from_its_words_and_the_apps_rows_and_kept_apart(store):
    cfg = load()
    cid = await _daydream(store)
    await _turn(store, cid, feel=True)
    answer = _says('{"emotion": "relieved", "strength": 0.5, "why": "it built", "needs": '
                   '[{"need": "competence", "change": "met", "strength": 0.4}, '
                   '{"need": "intimacy", "change": "stirred", "strength": 0.9}]}')
    row = await mind_shadow.appraise(_app(store, cfg), cid, "t1", backend=answer)
    assert row is not None and row["kind"] == "own time" and row["emotion"] == "relieved"
    assert json.loads(row["needs"]) == [{"need": "competence", "change": "met",
                                         "strength": 0.4}], "intimacy only in a chat"
    assert row["named"] == "proud 0.6: it built"
    user = answer.calls[0][1]["content"]
    assert "own time (Daydream)" in user and "trying the build once more" in user
    assert "it finally built after two days" in user
    assert "Error 2" in user, "a failed call's first result line, as its row says it"
    assert "SECRET DRAFT" not in user and "tender" not in user, "no thinking, no prompt"
    assert "competence" in answer.calls[0][0]["content"]
    assert "intimacy" not in answer.calls[0][0]["content"]
    assert await store.read(lambda c: c.execute("SELECT count(*) FROM moods").fetchone()[0]) \
        == 0
    assert await store.read(lambda c: c.execute("SELECT count(*) FROM needs").fetchone()[0]) \
        == 0, "nothing moved"
    text = await store.read(lambda c: mind_shadow.view(c, cfg, 1, T0 + timedelta(hours=1)))
    assert text.startswith("1 of your turns read in shadow (own time 1), a feeling named "
                           "in 1. None of it moved your mood or your needs."), text
    assert "relieved 0.5 (it built); needs: competence met 0.4. You named: proud 0.6: " \
           "it built." in text


async def test_nothing_named_is_kept_and_an_unreadable_answer_is_not(store):
    cfg = load()
    cid = await _daydream(store)
    await _turn(store, cid)
    row = await mind_shadow.appraise(_app(store, cfg), cid, "t1",
                                     backend=_says('{"emotion": null, "needs": []}'))
    assert row is not None and row["emotion"] is None and row["named"] is None
    assert await mind_shadow.appraise(_app(store, cfg), cid, "t1",
                                      backend=_says("no idea")) is None
    assert await store.read(lambda c: c.execute(
        "SELECT count(*) FROM shadow_feelings").fetchone()[0]) == 1


async def test_a_chat_turn_is_read_only_when_it_named_its_feeling_and_a_sub_agent_never(store):
    cfg = load()
    chat = await conversations.create(store)
    await _turn(store, chat)
    assert await store.read(lambda c: mind_shadow.facts(c, chat, "t1")) is None, \
        "the live appraisal reads it"
    await store.write(lambda c: c.execute(
        "INSERT INTO tool_calls(id, message_id, name, arguments, state) VALUES('tc2', 'a1', "
        "'mind__feel', ?, 'ok')", (json.dumps({"emotion": "proud", "strength": 0.6}),)))
    answer = _says('{"emotion": "annoyed", "strength": 0.3, "why": "the build"}')
    row = await mind_shadow.appraise(_app(store, cfg), chat, "t1", backend=answer)
    assert row is not None and row["kind"] == "chat" and row["named"] == "proud 0.6"
    assert "jealous" in answer.calls[0][0]["content"], "the live appraisal's own prompt"
    sub = await conversations.create(store, agent_of=chat, kind="agent")
    assert await store.read(lambda c: mind_shadow.facts(c, sub, "t1")) is None


async def test_the_list_shows_the_readings_that_moved_its_mood_before_the_shadow_ones(store):
    """mind__list_appraisals: the live appraisal's readings after its chat turns, which
    moved the mood and the needs, come first and say so; the shadow ones say they moved
    nothing; the agent's own feeling is in neither."""
    cfg = load()
    at = (T0 + timedelta(minutes=5)).isoformat()
    chat = await conversations.create(store)

    def fn(c):
        for mid in ("reply1", "reply2", "reply3"):
            c.execute("INSERT INTO messages(id, conversation_id, role, content, created_at) "
                      "VALUES (?, ?, 'assistant', 'a reply', ?)", (mid, chat, at))
        c.execute("INSERT INTO moods(at, emotion, strength, why, by, message_id) VALUES "
                  "(?, 'annoyed', 0.5, 'reads the joke as a jab', 'appraisal', 'reply1')", (at,))
        c.execute("INSERT INTO needs(at, need, change, strength, why, by, message_id) VALUES "
                  "(?, 'connection', 'met', 0.3, 'reads the joke as a jab', 'appraisal', "
                  "'reply1')", (at,))
        c.execute("INSERT INTO needs(at, need, change, strength, why, by, message_id) VALUES "
                  "(?, 'novelty', 'met', 0.2, 'a new game', 'appraisal', 'reply2')", (at,))
        c.execute("INSERT INTO moods(at, emotion, strength, why, by, message_id) VALUES "
                  "(?, 'curious', 0.7, 'a new game', 'agent', 'reply3')", (at,))
        c.execute("INSERT INTO shadow_feelings(at, kind, emotion, strength, why, needs, named) "
                  "VALUES (?, 'own time', 'relieved', 0.4, 'it built', '[]', NULL)", (at,))
    await store.write(fn)
    text = await store.read(lambda c: mind_shadow.view(c, cfg, 1, T0 + timedelta(hours=1)))
    live, shadow = text.split("\n\n1 of your turns read in shadow")
    assert live.startswith("2 of your chat turns read as they ended, each reading moving "
                           "your mood or your needs:"), live
    assert "chat: annoyed 0.5 (reads the joke as a jab); needs: connection met 0.3." in live
    assert "chat: no feeling (a new game); needs: novelty met 0.2." in live
    assert "curious" not in text, "its own feeling is not the appraiser's"
    assert shadow.startswith(" (own time 1), a feeling named in 1. None of it moved your "
                             "mood or your needs."), shadow


async def test_off_it_queues_nothing(store):
    cfg = load()
    cid = await _daydream(store)
    await _turn(store, cid)
    put: list = []
    r = cfg.r
    cfg.r = lambda k: False if k == "shadow_appraisal" else r(k)
    app = _app(store, cfg)
    app.dispatcher = SimpleNamespace(work=SimpleNamespace(put=lambda *a, **k: put.append(a)))
    await mind_shadow.ended(app, cid, "t1")
    assert put == []
